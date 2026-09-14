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
  """Identifica as linhas reais de chamadas filtrando pelo padrão de data e hora 'DD/MM HH:MM'."""
  loc = ctx.locator("tr").filter(
      has_text=re.compile(r"\d{2}/\d{2}\s+\d{2}:\d{2}")
  )
  qtd = loc.count()
  if qtd > 0:
    log_msg(f"[GRID] {qtd} chamadas confirmadas na tabela.")
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

  btn_next = ctx.locator(
      ".pDiv .pNext, .pNext, div.pButton.pNext, a.pNext"
  ).first
  if btn_next.count() > 0:
    try:
      btn_next.click(force=True)
    except Exception as e:
      log_msg(f"Clique Playwright no .pNext: {e}")

  ctx.evaluate("""() => {
        if (window.jQuery && window.jQuery('.pNext').length) {
            window.jQuery('.pNext').click();
        }
    }""")

  try:
    expr = (
        """() => {
            const reload = document.querySelector('.pReload');
            const estaCarregando = reload && reload.classList.contains('loading');
            
            let paginaMudou = false;
            const els = Array.from(document.querySelectorAll('.pDiv, .pGroup, .pcontrol, div, span, b, td'));
            for (const el of els) {
                const txt = el.innerText ? el.innerText.trim() : '';
                const m = txt.match(/^(\\d+)\\s*\\/\\s*(\\d+)$/);
                if (m) {
                    const [matchFull, pAtual, pTotal] = m;
                    if (parseInt(pAtual, 10) === """
        + str(proxima)
        + """) {
                        paginaMudou = true;
                        break;
                    }
                }
            }
            return paginaMudou && !estaCarregando;
        }"""
    )
    ctx.wait_for_function(expr, timeout=15000)
    log_msg(f"-> Sucesso: Página {proxima} confirmada no DOM.")
    ctx.wait_for_timeout(1500)
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

        idx_data = -1
        for i, td in enumerate(tds):
          if re.search(r"\d{2}/\d{2}\s+\d{2}:\d{2}", td.inner_text().strip()):
            idx_data = i
            break

        if idx_data < 0 or len(tds) < idx_data + 5:
          continue

        idx_dur = idx_data + 1
        idx_orig = idx_data + 2
        idx_dest = idx_data + 3
        idx_stat = idx_data + 4

        data_hora = tds[idx_data].inner_text().strip()
        duracao = tds[idx_dur].inner_text().strip()
        origem = tds[idx_orig].inner_text().strip()
        destino = tds[idx_dest].inner_text().strip()
        status = tds[idx_stat].inner_text().strip()

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
            log_msg(f"[{nome} #{idx}] Abrindo balão de áudio ({duracao})...")
            linha.scroll_into_view_if_needed()

            if link_audio.count() > 0:
              link_audio.click(force=True)
            else:
              img_audio.click(force=True)

            ctx.wait_for_timeout(600)

            # Localiza o botão salvar (<img title="Salvar" ...>)
            btn_salvar = coluna_audio.locator(
                "img[title*='alvar'], img[src*='save'], a:has(img[src*='save'])"
            ).first
            try:
              btn_salvar.wait_for(state="attached", timeout=4000)
            except Exception:
              btn_salvar = ctx.locator(
                  "img[title*='alvar'], img[src*='save'],"
                  " a:has(img[src*='save'])"
              ).last

            href = ""
            try:
              href = btn_salvar.evaluate(
                  "el => (el.closest('a') ? el.closest('a').href : '') ||"
                  " el.src || ''"
              )
            except Exception:
              pass

            if (
                href
                and (
                    ".php" in href
                    or "download" in href
                    or ".gsm" in href
                    or ".wav" in href
                )
                and not href.endswith(".gif")
            ):
              url_download = (
                  href
                  if href.startswith("http")
                  else f"{PBX_URL.rstrip('/')}/{href.lstrip('/')}"
              )
              log_msg(
                  f"[{nome} #{idx}] Baixando áudio via requisição direta:"
                  f" {url_download}"
              )
              resp = page.request.get(url_download)
              with open(caminho_gsm, "wb") as f_out:
                f_out.write(resp.body())
            else:
              log_msg(f"[{nome} #{idx}] Disparando download via JS...")
              with page.expect_download(timeout=15000) as download_info:
                btn_salvar.evaluate(
                    "el => (el.closest('a') ? el.closest('a') : el).click()"
                )
              download = download_info.value
              download.save_as(caminho_gsm)

            if os.path.exists(caminho_gsm) and os.path.getsize(caminho_gsm) > 0:
              tam = os.path.getsize(caminho_gsm)
              log_msg(f"[{nome} #{idx}] Áudio GSM salvo ({tam} bytes).")
              converter_gsm_para_wav(caminho_gsm, caminho_wav)

              log_msg(f"[{nome} #{idx}] Transcrevendo com Whisper...")
              resultado = model_whisper.transcribe(caminho_wav, language="pt")
              transcricao = resultado.get("text", "").strip()
              log_msg(f"[{nome} #{idx}] Transcrição finalizada com sucesso.")
            else:
              log_msg(f"[{nome} #{idx}] Arquivo de áudio baixado vazio.")
              transcricao = "Gravação com tamanho 0 bytes"

            # Fecha o balão para a próxima linha
            ctx.keyboard.press("Escape")

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
