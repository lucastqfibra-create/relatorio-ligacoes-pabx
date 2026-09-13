from datetime import datetime, timedelta
import math
import os
import re
import subprocess
import time
import pandas as pd
from playwright.sync_api import sync_playwright
import whisper


def limpar_url(raw_url):
  if not raw_url:
    return "http://177.10.116.84"
  url = raw_url.strip()
  if "](" in url:
    url = url.split("]")[-1].rstrip(")")
  m = re.search(r"https?://[^\s\)\"\'\[\]]+", url)
  if m:
    url = m.group(0)
  else:
    url = url.strip("\"'<>[]() ")
    if not url.startswith("http://") and not url.startswith("https://"):
      url = f"http://{url}"
  return url.rstrip("/")


PBX_URL = limpar_url(os.getenv("PBX_URL", "http://177.10.116.84"))
PBX_USER = os.getenv("PBX_USER", "lucas").strip().strip("\"'")
PBX_PASSWORD = os.getenv("PBX_PASSWORD", "lcsu251535").strip().strip("\"'")

RAMAIS = [
    {"numero": "2003", "nome": "Fernanda"},
    {"numero": "2005", "nome": "Julia"},
]

DATA_CONSULTA = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
URL_CONTAINER = f"{PBX_URL}/pbxip/framework/container.php?token=MAIN/cmVwb3J0LmNhbGxyZWNvcmRldGFpbFg="

OUTPUT_DIR = "ligacoes"
AUDIO_DIR = os.path.join(OUTPUT_DIR, "audios")
os.makedirs(AUDIO_DIR, exist_ok=True)


def converter_gsm_para_wav(gsm_path, wav_path):
  cmd = ["ffmpeg", "-y", "-i", gsm_path, wav_path]
  subprocess.run(
      cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
  )


def obter_info_paginacao(page):
  """Inspeciona a paginação do Flexigrid no DOM de múltiplas formas para obter

  a página atual e o total real de páginas.
  """
  try:
    dados = page.evaluate("""() => {
            const pcontrol = document.querySelector('.pDiv .pcontrol, .pcontrol');
            const pagestat = document.querySelector('.pDiv .pPageStat, .pPageStat');
            const pcontrolSpan = document.querySelector('.pDiv .pcontrol span, .pcontrol span');
            const pcontrolInput = document.querySelector('.pDiv .pcontrol input, .pcontrol input');
            const selectRp = document.querySelector('.pDiv select[name="rp"], select[name="rp"]');

            return {
                pcontrol_text: pcontrol ? pcontrol.innerText.trim() : '',
                pcontrol_span: pcontrolSpan ? pcontrolSpan.innerText.trim() : '',
                pagestat_text: pagestat ? pagestat.innerText.trim() : '',
                input_val: pcontrolInput ? pcontrolInput.value.trim() : '1',
                rp_val: selectRp ? selectRp.value.trim() : '15'
            };
        }""")

    pagina_atual = (
        int(dados.get("input_val", "1"))
        if dados.get("input_val", "").isdigit()
        else 1
    )
    total_paginas = 1

    # 1. Tenta extrair do span interno de pcontrol
    span_txt = dados.get("pcontrol_span", "")
    if span_txt.isdigit() and int(span_txt) > 1:
      total_paginas = int(span_txt)

    # 2. Se não achou, analisa o texto completo do pcontrol (ex: "Página 1 de 4")
    if total_paginas == 1:
      nums_pcontrol = re.findall(r"\d+", dados.get("pcontrol_text", ""))
      if len(nums_pcontrol) >= 2:
        total_paginas = int(nums_pcontrol[-1])

    # 3. Se ainda não achou, analisa o pPageStat (ex: "Exibindo de 1 a 15 de 43 registros")
    if total_paginas == 1:
      nums_stat = re.findall(r"\d+", dados.get("pagestat_text", ""))
      if len(nums_stat) >= 3:
        por_pag = int(nums_stat[1]) - int(nums_stat[0]) + 1
        total_reg = int(nums_stat[-1])
        if por_pag > 0 and total_reg > 0:
          total_paginas = math.ceil(total_reg / por_pag)

    print(
        f"[PAGINAÇÃO DETECTADA] Página Atual: {pagina_atual} | Total Páginas:"
        f" {total_paginas} | Detalhes DOM: {dados}"
    )
    return {"pagina_atual": pagina_atual, "total_paginas": total_paginas}
  except Exception as e:
    print(f"Aviso ao ler paginação: {e}")
    return {"pagina_atual": 1, "total_paginas": 1}


def avancar_proxima_pagina_flexigrid(page, pagina_atual):
  """Avança a paginação do Flexigrid sobrescrevendo o estado interno do componente

  (this.p.newp) e acionando o método populate() diretamente, com fallbacks de UI
  e sincronização rigorosa do carregamento assíncrono.
  """
  proxima = pagina_atual + 1
  print(
      f"-> Acionando transição da página {pagina_atual} para a página"
      f" {proxima}..."
  )

  primeira_linha = page.locator("tr[id^='tr_']").first
  id_anterior = (
      primeira_linha.get_attribute("id") if primeira_linha.count() > 0 else ""
  )

  # 1. Disparo direto via API do Flexigrid no contexto da página
  sucesso_js = page.evaluate(
      """(targetPage) => {
        let disparou = false;
        
        // Localiza tabelas que possuem a instância do Flexigrid anexada
        const tabelas = window.jQuery ? window.jQuery('table') : [];
        if (tabelas.length) {
            tabelas.each(function() {
                if (this.grid && this.p) {
                    this.grid.loading = false; // Destrava eventual flag pendente
                    this.p.newp = targetPage;  // Define a nova página alvo
                    this.grid.populate();      // Dispara a requisição AJAX
                    disparou = true;
                }
            });
        }
        
        // Fallback: se não encontrou this.grid, preenche o input e envia evento Enter
        if (!disparou) {
            const input = document.querySelector('.pDiv .pcontrol input, .pcontrol input');
            if (input) {
                input.value = targetPage;
                const event = new KeyboardEvent('keydown', {
                    bubbles: true,
                    cancelable: true,
                    keyCode: 13,
                    which: 13
                });
                input.dispatchEvent(event);
                disparou = true;
            }
        }
        
        return disparou;
    }""",
      proxima,
  )

  # 2. Se o JavaScript não conseguiu acionar, tenta via Playwright no input
  if not sucesso_js:
    input_pag = page.locator(".pDiv .pcontrol input, .pcontrol input").first
    if input_pag.is_visible():
      try:
        input_pag.fill(str(proxima))
        input_pag.press("Enter")
      except Exception as e:
        print(f"Tentativa via input Playwright falhou: {e}")

  # 3. Aguarda a confirmação de que o DOM atualizou
  try:
    page.wait_for_function(
        """({ targetPage, oldRowId }) => {
            const inp = document.querySelector('.pDiv .pcontrol input, .pcontrol input');
            const tr = document.querySelector("tr[id^='tr_']");
            const reload = document.querySelector('.pReload');
            
            const estaCarregando = reload && reload.classList.contains('loading');
            const paginaMudou = inp && parseInt(inp.value, 10) === targetPage;
            const linhaMudou = oldRowId ? (tr && tr.id !== oldRowId) : true;
            
            return paginaMudou && linhaMudou && !estaCarregando;
        }""",
        {"targetPage": proxima, "oldRowId": id_anterior},
        timeout=15000,
    )
    print(f"-> Sucesso: Página {proxima} confirmada e carregada no DOM.")
    page.wait_for_timeout(1000)
    return True
  except Exception as e:
    print(
        f"Aviso: Não foi possível confirmar a transição para a página {proxima}"
        f" ({e})."
    )
    return False


def login_pabx(page):
  print(f"Navegando para a página de login: {PBX_URL}")
  page.goto(PBX_URL, timeout=60000)
  page.wait_for_load_state("domcontentloaded")
  page.wait_for_timeout(2000)

  campo_usuario = page.locator(
      "input[type='text'], input[name='user'], input[name='login'], #src,"
      " #user, #login"
  ).first
  campo_senha = page.locator("input[type='password']").first

  if not campo_senha.is_visible():
    for frame in page.frames:
      f_pass = frame.locator("input[type='password']").first
      if f_pass.is_visible():
        f_user = frame.locator("input[type='text']").first
        print(
            "Formulário de login localizado dentro do frame:"
            f" {frame.name or frame.url}"
        )
        f_user.fill(PBX_USER)
        f_pass.fill(PBX_PASSWORD)
        f_pass.press("Enter")
        page.wait_for_timeout(3000)
        return

  if campo_senha.is_visible():
    print("Preenchendo credenciais de acesso...")
    campo_usuario.fill(PBX_USER)
    campo_senha.fill(PBX_PASSWORD)

    btn_submit = page.locator(
        "button[type='submit'], input[type='submit'], #submit, .btn-primary"
    ).first
    if btn_submit.is_visible():
      btn_submit.click()
    else:
      campo_senha.press("Enter")

    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    print("Login efetuado.")


def aplicar_filtros(page, ramal_numero):
  print(f"Acessando módulo de registros: {URL_CONTAINER}")
  page.goto(URL_CONTAINER, timeout=60000)
  page.wait_for_selector("#src", timeout=25000)

  if page.locator("#calldate_day_start").is_visible():
    page.locator("#calldate_day_start").fill(DATA_CONSULTA)
  if page.locator("#calldate_day_end").is_visible():
    page.locator("#calldate_day_end").fill(DATA_CONSULTA)

  page.fill("#src", ramal_numero)

  for s in page.locator("select").all():
    for opt in s.locator("option").all():
      txt = opt.inner_text().strip().lower()
      val = opt.get_attribute("value")
      if txt in ["saínte", "sainte", "saída", "saida"]:
        s.select_option(value=val)
        break
      elif txt in ["atendida", "atendidas"]:
        s.select_option(value=val)
        break

  print("Disparando consulta (#confirm)...")
  page.click("#confirm")
  page.wait_for_timeout(4000)

  try:
    page.wait_for_selector(
        "tr[id^='tr_'], .pDiv .pcontrol, .pcontrol", timeout=15000
    )
  except Exception:
    pass
  page.wait_for_timeout(1000)


def processar_chamadas(page, model_whisper):
  registros = []

  for ramal in RAMAIS:
    num = ramal["numero"]
    nome = ramal["nome"]
    print(f"\n==========================================")
    print(f"Consultando Ramal {num} ({nome}) para o dia {DATA_CONSULTA}")
    print(f"==========================================")

    aplicar_filtros(page, num)

    pagina_atual = 1
    while True:
      info_pag = obter_info_paginacao(page)
      total_paginas = info_pag["total_paginas"]

      try:
        page.wait_for_selector("tr[id^='tr_']", timeout=8000)
        linhas = page.locator("tr[id^='tr_']").all()
      except Exception:
        linhas = []

      print(
          f"\n--- [{nome}] Processando Página {pagina_atual} de {total_paginas}"
          f" ({len(linhas)} chamadas) ---"
      )

      for idx, linha in enumerate(linhas, start=1):
        tds = linha.locator("td").all()
        if len(tds) < 10:
          continue

        data_hora = tds[0].inner_text().strip()
        duracao = tds[1].inner_text().strip()
        origem = tds[2].inner_text().strip()
        destino = tds[3].inner_text().strip()
        status = tds[4].inner_text().strip()

        icone_audio = tds[9].locator("a > img")
        transcricao = "Sem gravação"
        data_formatada = (
            data_hora.replace("/", "-").replace(":", "-").replace(" ", "_")
        )
        arquivo_base = f"{num}_{nome}_{data_formatada}_p{pagina_atual}_{idx}"
        caminho_gsm = os.path.join(AUDIO_DIR, f"{arquivo_base}.gsm")
        caminho_wav = os.path.join(AUDIO_DIR, f"{arquivo_base}.wav")

        if icone_audio.count() > 0:
          try:
            icone_audio.first.click()
            page.wait_for_timeout(500)
            btn_salvar = tds[9].locator(
                "div img, img[alt*='Salvar'], img[title*='Salvar']"
            ).first

            with page.expect_download(timeout=15000) as download_info:
              btn_salvar.click()

            download = download_info.value
            download.save_as(caminho_gsm)

            converter_gsm_para_wav(caminho_gsm, caminho_wav)

            resultado = model_whisper.transcribe(caminho_wav, language="pt")
            transcricao = resultado.get("text", "").strip()
          except Exception as e:
            print(
                f"Erro ao processar áudio da chamada {idx} (Pág"
                f" {pagina_atual}): {e}"
            )
            transcricao = f"Falha no download/transcrição: {str(e)}"

        registros.append({
            "Ramal": num,
            "Operador": nome,
            "Data/Hora": data_hora,
            "Duração": duracao,
            "Origem": origem,
            "Destino": destino,
            "Status": status,
            "Transcrição": transcricao,
            "Arquivo Áudio": (
                f"{arquivo_base}.wav" if os.path.exists(caminho_wav) else "N/A"
            ),
        })

      # Se chegamos ou ultrapassamos o total confirmado de páginas
      if total_paginas > 1 and pagina_atual >= total_paginas:
        print(
            f"[{nome}] Concluído: todas as {total_paginas} páginas foram"
            " processadas."
        )
        break

      # Avança para a próxima página
      avancou = avancar_proxima_pagina_flexigrid(page, pagina_atual)
      if not avancou:
        print(f"[{nome}] Fim da paginação após página {pagina_atual}.")
        break

      pagina_atual += 1

  return registros


def gerar_relatorios(registros):
  df = pd.DataFrame(registros)
  data_formatada = DATA_CONSULTA.replace("/", "-")
  csv_path = os.path.join(OUTPUT_DIR, f"relatorio_ligacoes_{data_formatada}.csv")
  md_path = os.path.join(OUTPUT_DIR, f"relatorio_ligacoes_{data_formatada}.md")

  df.to_csv(csv_path, index=False, encoding="utf-8-sig")

  with open(md_path, "w", encoding="utf-8") as f:
    f.write(f"# Relatório Consolidado de Ligações PABX - {DATA_CONSULTA}\n\n")
    f.write(f"Total de chamadas processadas: {len(df)}\n\n")
    for r in registros:
      f.write(
          f"### Atendimento {r['Operador']} (Ramal {r['Ramal']}) -"
          f" {r['Data/Hora']}\n"
      )
      f.write(f"- **Destino:** {r['Destino']}\n")
      f.write(f"- **Duração:** {r['Duração']}\n")
      f.write(f"- **Status:** {r['Status']}\n")
      f.write(f"- **Transcrição:**\n> {r['Transcrição']}\n\n---\n")

  print(f"\nRelatórios gerados em:\n- {csv_path}\n- {md_path}")


def main():
  print("Carregando modelo Whisper base...")
  model_whisper = whisper.load_model("base")

  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    login_pabx(page)
    registros = processar_chamadas(page, model_whisper)
    gerar_relatorios(registros)

    browser.close()


if __name__ == "__main__":
  main()
