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

OUTPUT_DIR = "ligacoes"
AUDIO_DIR = os.path.join(OUTPUT_DIR, "audios")
os.makedirs(AUDIO_DIR, exist_ok=True)
LOG_FILE = os.path.join(OUTPUT_DIR, "log_execucao.txt")


def log_msg(msg):
  timestamp = datetime.now().strftime("%H:%M:%S")
  linha_log = f"{timestamp} - {msg}"
  print(linha_log)
  try:
    with open(LOG_FILE, "a", encoding="utf-8") as f:
      f.write(linha_log + "\n")
  except Exception:
    pass


def normalizar_data(data_str):
  if not data_str:
    return ""
  data_clean = data_str.strip()
  digitos = "".join(c for c in data_clean if c.isdigit())
  if len(digitos) == 8:
    if int(digitos[:4]) > 1900:
      return f"{digitos[6:8]}/{digitos[4:6]}/{digitos[:4]}"
    return f"{digitos[0:2]}/{digitos[2:4]}/{digitos[4:8]}"
  if "-" in data_clean:
    partes = data_clean.split("-")
    if len(partes) == 3:
      p_a, p_b, p_c = partes
      if len(p_a) == 4:
        return f"{p_c}/{p_b}/{p_a}"
      return f"{p_a}/{p_b}/{p_c}"
  return data_clean


def definir_data_consulta():
  data_env = os.getenv("DATA_MANUAL", "").strip()
  if data_env:
    data_norm = normalizar_data(data_env)
    log_msg(f"[CONFIG] Utilizando data manual normalizada: {data_norm}")
    return data_norm

  data = datetime.now() - timedelta(days=1)
  while data.weekday() in (5, 6):
    data -= timedelta(days=1)
  data_calculada = data.strftime("%d/%m/%Y")
  log_msg(f"[CONFIG] Data da consulta (último dia útil): {data_calculada}")
  return data_calculada


DATA_CONSULTA = definir_data_consulta()
URL_CONTAINER = f"{PBX_URL}/pbxip/framework/container.php?token=MAIN/cmVwb3J0LmNhbGxzLmRldGFpbGVk"


def converter_gsm_para_wav(gsm_path, wav_path):
  cmd = ["ffmpeg", "-y", "-i", gsm_path, wav_path]
  subprocess.run(
      cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
  )


def obter_contexto_registros(page, timeout=30000):
  start = time.time()
  while time.time() - start < (timeout / 1000):
    try:
      if page.locator("#src").count() > 0 and page.locator("#src").is_visible():
        return page
    except Exception:
      pass

    for frame in page.frames:
      try:
        if (
            frame.locator("#src").count() > 0
            and frame.locator("#src").is_visible()
        ):
          log_msg(f"Módulo de registros localizado no frame: {frame.name}")
          return frame
      except Exception:
        pass

    page.wait_for_timeout(1000)

  screenshot_path = os.path.join(OUTPUT_DIR, "erro_timeout_src.png")
  try:
    page.screenshot(path=screenshot_path)
  except Exception:
    pass
  raise TimeoutError(
      f"Campo #src não foi encontrado após {timeout}ms na URL {page.url}."
  )


def obter_linhas_tabela(ctx):
  """Identifica as linhas reais de chamadas filtrando linhas que contêm

  o padrão de data 'DD/MM' na primeira célula.
  """
  todas_linhas = ctx.locator("tr").all()
  linhas_chamadas = []

  for linha in todas_linhas:
    tds = linha.locator("td").all()
    if len(tds) >= 5:
      primeira_celula = next(iter(tds)).inner_text().strip()
      if re.match(r"^\d{2}/\d{2}", primeira_celula):
        linhas_chamadas.append(linha)

  if len(linhas_chamadas) > 0:
    log_msg(
        f"[GRID] {len(linhas_chamadas)} chamadas confirmadas na tabela atual."
    )
    return linhas_chamadas

  for sel in [".bDiv tbody tr", ".bDiv tr", "tr[id^='row']"]:
    loc = ctx.locator(sel)
    if loc.count() > 0 and loc.first.locator("td").count() >= 5:
      log_msg(f"[GRID] Fallback: {loc.count()} chamadas pelo seletor '{sel}'.")
      return loc.all()

  log_msg("[GRID] Nenhuma linha de chamada encontrada na tela.")
  return []


def obter_info_paginacao(ctx):
  try:
    dados = ctx.evaluate("""() => {
            let pagAtual = 1;
            let totalPags = 1;
            let textoEncontrado = '';

            const elementos = Array.from(document.querySelectorAll('.pDiv, .pGroup, .pcontrol, div, span, b, td'));
            for (const el of elementos) {
                const txt = el.innerText ? el.innerText.trim() : '';
                const m = txt.match(/^(\\d+)\\s*\\/\\s*(\\d+)$/);
                if (m) {
                    const [matchFull, pAtual, pTotal] = m;
                    pagAtual = parseInt(pAtual, 10);
                    totalPags = parseInt(pTotal, 10);
                    textoEncontrado = txt;
                    break;
                }
            }
            return { pagina_atual: pagAtual, total_paginas: totalPags, texto: textoEncontrado };
        }""")

    pag_atual = dados.get("pagina_atual", 1)
    tot_pags = dados.get("total_paginas", 1)
    log_msg(
        f"[PAGINAÇÃO] Página {pag_atual} de {tot_pags} (Indicador:"
        f" '{dados.get('texto', '')}')"
    )
    return {"pagina_atual": pag_atual, "total_paginas": tot_pags}
  except Exception as e:
    log_msg(f"Aviso ao ler paginação: {e}")
    return {"pagina_atual": 1, "total_paginas": 1}


def avancar_proxima_pagina_flexigrid(ctx, pagina_atual):
  proxima = pagina_atual + 1
  log_msg(
      f"-> Acionando avanço da página {pagina_atual} para a página {proxima}..."
  )

  linhas_antes = obter_linhas_tabela(ctx)
  texto_linha_anterior = (
      next(iter(linhas_antes)).inner_text().strip() if linhas_antes else ""
  )

  clicou = False
  btn_next = ctx.locator(
      ".pDiv .pNext, .pNext, div.pButton.pNext, a.pNext"
  ).first
  if btn_next.count() > 0 and btn_next.is_visible():
    try:
      btn_next.click(force=True)
      clicou = True
    except Exception as e:
      log_msg(f"Clique Playwright no .pNext: {e}")

  if not clicou:
    ctx.evaluate("""() => {
            if (window.jQuery && window.jQuery('.pNext').length) {
                window.jQuery('.pNext').click();
            } else {
                const el = document.querySelector('.pNext');
                if (el) el.click();
            }
        }""")

  try:
    ctx.wait_for_function(
        """({ targetPage, oldRowText }) => {
                const reload = document.querySelector('.pReload');
                const estaCarregando = reload && reload.classList.contains('loading');
                
                let paginaMudou = false;
                const els = Array.from(document.querySelectorAll('.pDiv, .pGroup, .pcontrol, div, span, b, td'));
                for (const el of els) {
                    const txt = el.innerText ? el.innerText.trim() : '';
                    const m = txt.match(/^(\\d+)\\s*\\/\\s*(\\d+)$/);
                    if (m) {
                        const [matchFull, pAtual, pTotal] = m;
                        if (parseInt(pAtual, 10) === targetPage) {
                            paginaMudou = true;
                            break;
                        }
                    }
                }
                
                const primeiraLinha = document.querySelector('tr td');
                const trPai = primeiraLinha ? primeiraLinha.closest('tr') : null;
                const linhaMudou = oldRowText ? (trPai && trPai.innerText.trim() !== oldRowText) : true;
                
                return (paginaMudou || linhaMudou) && !estaCarregando;
            }""",
        {"targetPage": proxima, "oldRowText": texto_linha_anterior},
        timeout=15000,
    )
    log_msg(f"-> Sucesso: Página {proxima} confirmada no DOM.")
    ctx.wait_for_timeout(1000)
    return True
  except Exception as e:
    log_msg(
        "Aviso: Não foi possível confirmar a transição para a página"
        f" {proxima} ({e})."
    )
    return False


def login_pabx(page):
  log_msg(f"Navegando para a página de login: {PBX_URL}")
  page.goto(PBX_URL, timeout=60000)
  page.wait_for_load_state("domcontentloaded")
  page.wait_for_timeout(2000)

  campo_usuario = None
  campo_senha = None
  ctx_login = page

  if page.locator("input[type='password']").count() > 0:
    campo_senha = page.locator("input[type='password']").first
    campo_usuario = page.locator(
        "input[type='text'], input[name='user'], input[name='login'], #src,"
        " #user, #login"
    ).first
  else:
    for frame in page.frames:
      if frame.locator("input[type='password']").count() > 0:
        campo_senha = frame.locator("input[type='password']").first
        campo_usuario = frame.locator(
            "input[type='text'], input[name='user'], input[name='login'], #src,"
            " #user, #login"
        ).first
        ctx_login = frame
        log_msg(f"Formulário de login localizado no frame: {frame.name}")
        break

  if not campo_senha:
    log_msg("Aviso: Campo de senha não localizado.")
    return

  log_msg("Preenchendo credenciais de acesso...")
  campo_usuario.fill(PBX_USER)
  campo_senha.fill(PBX_PASSWORD)

  btn_submit = ctx_login.locator(
      "button[type='submit'], input[type='submit'], #submit, .btn-primary"
  ).first
  if btn_submit.count() > 0 and btn_submit.is_visible():
    btn_submit.click()
  else:
    campo_senha.press("Enter")

  try:
    campo_senha.wait_for(state="hidden", timeout=15000)
  except Exception:
    pass

  page.wait_for_load_state("networkidle")
  page.wait_for_timeout(2000)
  log_msg("Login efetuado com sucesso.")


def aplicar_filtros(page, ramal_numero):
  log_msg(f"Acessando módulo de registros: {URL_CONTAINER}")
  page.goto(URL_CONTAINER, timeout=60000)
  page.wait_for_load_state("domcontentloaded")
  page.wait_for_timeout(2000)

  ctx = obter_contexto_registros(page)

  for sel in [
      "#calldate_day_start",
      "#calldate_start",
      "input[name*='date_start']",
  ]:
    if ctx.locator(sel).count() > 0 and ctx.locator(sel).is_visible():
      ctx.locator(sel).fill(DATA_CONSULTA)
      page.keyboard.press("Escape")
      break

  for sel in ["#calldate_day_end", "#calldate_end", "input[name*='date_end']"]:
    if ctx.locator(sel).count() > 0 and ctx.locator(sel).is_visible():
      ctx.locator(sel).fill(DATA_CONSULTA)
      page.keyboard.press("Escape")
      break

  ctx.evaluate("""() => {
        if (window.jQuery && window.jQuery.datepicker) {
            try { window.jQuery.datepicker._hideDatepicker(); } catch(e) {}
        }
    }""")

  for h_fim in ctx.locator(
      "select[name*='hour_end'], select[name*='hora_fim'],"
      " #calldate_hour_end"
  ).all():
    for opt in h_fim.locator("option").all():
      if opt.inner_text().strip() == "23":
        h_fim.select_option(value=opt.get_attribute("value"))
        break

  for m_fim in ctx.locator(
      "select[name*='minute_end'], select[name*='minuto_fim'],"
      " #calldate_minute_end"
  ).all():
    for opt in m_fim.locator("option").all():
      if opt.inner_text().strip() in ["59", "55"]:
        m_fim.select_option(value=opt.get_attribute("value"))
        break

  ctx.fill("#src", ramal_numero)

  for s in ctx.locator("select").all():
    for opt in s.locator("option").all():
      txt = opt.inner_text().strip().lower()
      val = opt.get_attribute("value")
      if any(t in txt for t in ["saínt", "saint", "saíd", "said"]):
        s.select_option(value=val)
        break
      elif any(t in txt for t in ["atendid", "answered"]):
        s.select_option(value=val)
        break

  log_msg(
      f"Disparando consulta (#confirm) para o ramal {ramal_numero} no dia"
      f" {DATA_CONSULTA}..."
  )
  ctx.click("#confirm")

  try:
    page.wait_for_timeout(1000)
    ctx.locator(".pReload.loading").wait_for(state="detached", timeout=20000)
    ctx.locator(".gBlock").wait_for(state="detached", timeout=20000)
  except Exception:
    pass

  page.wait_for_timeout(2000)

  screenshot_path = os.path.join(OUTPUT_DIR, f"consulta_{ramal_numero}.png")
  try:
    page.screenshot(path=screenshot_path)
    log_msg(f"Screenshot da consulta salvo em: {screenshot_path}")
  except Exception:
    pass

  return ctx


def processar_chamadas(page, model_whisper):
  registros = []

  for ramal in RAMAIS:
    num = ramal["numero"]
    nome = ramal["nome"]
    log_msg(f"\n==========================================")
    log_msg(f"Consultando Ramal {num} ({nome}) para o dia {DATA_CONSULTA}")
    log_msg(f"==========================================")

    ctx = aplicar_filtros(page, num)

    pagina_atual = 1
    while True:
      info_pag = obter_info_paginacao(ctx)
      total_paginas = info_pag["total_paginas"]

      linhas = obter_linhas_tabela(ctx)

      log_msg(
          f"\n--- [{nome}] Processando Página {pagina_atual} de {total_paginas}"
          f" ({len(linhas)} chamadas identificadas) ---"
      )

      for idx, linha in enumerate(linhas, start=1):
        tds = linha.locator("td").all()
        if len(tds) < 5:
          continue

        col_data, col_duracao, col_origem, col_destino, col_status = tds[0:5]
        data_hora = col_data.inner_text().strip()
        duracao = col_duracao.inner_text().strip()
        origem = col_origem.inner_text().strip()
        destino = col_destino.inner_text().strip()
        status = col_status.inner_text().strip()

        coluna_audio = tds[-1]
        link_audio = coluna_audio.locator("a").first
        img_audio = coluna_audio.locator("img").first
        tem_icone = (
            img_audio.count() > 0 and img_audio.is_visible()
        ) or link_audio.count() > 0

        transcricao = "Sem gravação"
        data_formatada = (
            data_hora.replace("/", "-").replace(":", "-").replace(" ", "_")
        )
        arquivo_base = f"{num}_{nome}_{data_formatada}_p{pagina_atual}_{idx}"
        caminho_gsm = os.path.join(AUDIO_DIR, f"{arquivo_base}.gsm")
        caminho_wav = os.path.join(AUDIO_DIR, f"{arquivo_base}.wav")

        if tem_icone and duracao != "00:00:00":
          try:
            log_msg(
                f"[{nome} #{idx}] Abrindo balão de áudio da chamada"
                f" ({duracao})..."
            )
            if link_audio.count() > 0:
              link_audio.click(force=True)
            else:
              img_audio.click(force=True)

            ctx.wait_for_timeout(800)

            # Busca o botão de salvar
            btn_salvar = ctx.locator(
                "img[alt*='alvar'], img[title*='alvar'], a[title*='alvar'],"
                " a[href*='download'], a[href*='audio'], img[src*='save'],"
                " img[src*='disk']"
            ).first
            if not (btn_salvar.count() > 0 and btn_salvar.is_visible()):
              div_balao = coluna_audio.locator("div")
              if div_balao.count() > 0:
                btn_salvar = div_balao.locator("a, img").last

            # Checa se existe link direto (href)
            href = ""
            if btn_salvar.count() > 0:
              href = btn_salvar.get_attribute(
                  "href"
              ) or btn_salvar.locator("xpath=..").get_attribute("href")

            if href and (
                "http" in href
                or ".php" in href
                or ".gsm" in href
                or ".wav" in href
            ):
              url_download = (
                  href
                  if href.startswith("http")
                  else f"{PBX_URL.rstrip('/')}/{href.lstrip('/')}"
              )
              log_msg(
                  f"[{nome} #{idx}] Baixando áudio via link direto:"
                  f" {url_download}"
              )
              resp = page.request.get(url_download)
              with open(caminho_gsm, "wb") as f_out:
                f_out.write(resp.body())
            else:
              log_msg(f"[{nome} #{idx}] Interceptando evento de download...")
              with page.expect_download(timeout=15000) as download_info:
                if btn_salvar.count() > 0 and btn_salvar.is_visible():
                  btn_salvar.click(force=True)
                else:
                  coluna_audio.locator("img").last.click(force=True)

              download = download_info.value
              download.save_as(caminho_gsm)

            if os.path.exists(caminho_gsm) and os.path.getsize(caminho_gsm) > 0:
              tam = os.path.getsize(caminho_gsm)
              log_msg(
                  f"[{nome} #{idx}] Áudio GSM salvo com sucesso ({tam} bytes)."
              )
              converter_gsm_para_wav(caminho_gsm, caminho_wav)

              log_msg(f"[{nome} #{idx}] Transcrevendo com Whisper...")
              resultado = model_whisper.transcribe(caminho_wav, language="pt")
              transcricao = resultado.get("text", "").strip()
              log_msg(f"[{nome} #{idx}] Transcrição finalizada com sucesso.")
            else:
              log_msg(f"[{nome} #{idx}] Arquivo de áudio baixado veio vazio.")
              transcricao = "Gravação com tamanho 0 bytes"

          except Exception as e:
            log_msg(f"[{nome} #{idx}] Erro ao baixar/transcrever áudio: {e}")
            transcricao = f"Falha no download/transcrição: {str(e)}"
        else:
          if duracao == "00:00:00":
            transcricao = "Sem gravação (duração 00:00:00)"
          else:
            transcricao = "Sem gravação disponível"

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

      if total_paginas > 1 and pagina_atual >= total_paginas:
        log_msg(
            f"[{nome}] Concluído: todas as {total_paginas} páginas foram"
            " processadas."
        )
        break

      if len(linhas) == 0:
        log_msg(
            f"[{nome}] Nenhuma linha encontrada na página {pagina_atual}."
        )
        break

      avancou = avancar_proxima_pagina_flexigrid(ctx, pagina_atual)
      if not avancou:
        log_msg(f"[{nome}] Fim da paginação após página {pagina_atual}.")
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

  log_msg(
      f"\nRelatórios gerados com sucesso ({len(df)} chamadas):\n-"
      f" {csv_path}\n- {md_path}"
  )


def main():
  log_msg("Iniciando rotina de gravações e transcrições do PABX...")
  log_msg("Carregando modelo Whisper base...")
  model_whisper = whisper.load_model("base")

  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    login_pabx(page)
    registros = processar_chamadas(page, model_whisper)
    gerar_relatorios(registros)

    browser.close()
  log_msg("Rotina concluída com sucesso.")


if __name__ == "__main__":
  main()
