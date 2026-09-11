import os
import re
import subprocess
import time
from datetime import datetime, timedelta
import pandas as pd
from playwright.sync_api import sync_playwright
import whisper


def limpar_url(raw_url):
    if not raw_url:
        return "http://177.10.116.84"
    url = raw_url.strip()
    if "](" in url:
        url = url.split("](")[-1].rstrip(")")
    m = re.search(r"https?://[^\s\)\"\'\[\]<>]+", url)
    if m:
        url = m.group(0)
    else:
        url = url.strip("\"'<>[]() ")
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"http://{url}"
    return url.rstrip("/")


PBX_URL = limpar_url(os.getenv("PBX_URL", "http://177.10.116.84/"))
PBX_USER = os.getenv("PBX_USER", "lucas").strip().strip("\"'")
PBX_PASSWORD = os.getenv("PBX_PASSWORD", "lcsu251535").strip().strip("\"'")

RAMAIS = [
    {"numero": "2003", "nome": "Fernanda"},
    {"numero": "2005", "nome": "Julia"}
]

# Período D-1
DATA_CONSULTA = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
URL_CONTAINER = f"{PBX_URL}/pbxip/framework/container.php?token=MAIN/cmVwb3J0LmNhbGxzLmRldGFpbGVk"

OUTPUT_DIR = "ligacoes"
AUDIO_DIR = os.path.join(OUTPUT_DIR, "audios")
os.makedirs(AUDIO_DIR, exist_ok=True)


def converter_gsm_para_wav(gsm_path, wav_path):
    cmd = ["ffmpeg", "-y", "-i", gsm_path, wav_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def obter_total_paginas(page):
    try:
        page.wait_for_selector(".pDiv .pcontrol span", timeout=10000)
        texto = page.locator(".pDiv .pcontrol span").inner_text().strip()
        numeros = re.findall(r"\d+", texto)
        return int(numeros[-1]) if numeros else 1
    except Exception:
        return 1


def mudar_pagina_flexigrid(page, proxima_pagina):
    print(f"-> Avançando para a página {proxima_pagina} no Flexigrid...")
    input_pag = page.locator(".pDiv .pcontrol input")
    
    if input_pag.is_visible():
        input_pag.click()
        input_pag.fill(str(proxima_pagina))
        input_pag.press("Enter")
    else:
        page.locator(".pDiv .pNext").click()

    page.wait_for_selector(".pDiv .pReload:not(.loading)", timeout=15000)
    page.wait_for_function(
        f"() => {{ const el = document.querySelector('.pDiv .pcontrol input'); return el && parseInt(el.value) === {proxima_pagina}; }}",
        timeout=15000
    )
    page.wait_for_timeout(1000)


def login_pabx(page):
    print(f"Navegando para a página de login: {PBX_URL}")
    page.goto(PBX_URL, timeout=60000)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2000)

    campo_usuario = page.locator("input[type='text'], input[name*='user'], input[name*='login'], #src, #user, #login").first
    campo_senha = page.locator("input[type='password']").first

    if not campo_senha.is_visible():
        for frame in page.frames:
            f_pass = frame.locator("input[type='password']").first
            if f_pass.is_visible():
                f_user = frame.locator("input[type='text']").first
                print(f"Formulário de login localizado dentro do frame: {frame.name or frame.url}")
                f_user.fill(PBX_USER)
                f_pass.fill(PBX_PASSWORD)
                f_pass.press("Enter")
                page.wait_for_timeout(3000)
                return

    if campo_senha.is_visible():
        print("Preenchendo credenciais de acesso...")
        campo_usuario.fill(PBX_USER)
        campo_senha.fill(PBX_PASSWORD)
        
        btn_submit = page.locator("button[type='submit'], input[type='submit'], #submit, .btn-primary").first
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

    # Preenchimento exato dos campos de data revelados no PABX
    if page.locator("#calldate_day_start").is_visible():
        page.locator("#calldate_day_start").fill(DATA_CONSULTA)
    if page.locator("#calldate_day_end").is_visible():
        page.locator("#calldate_day_end").fill(DATA_CONSULTA)

    page.fill("#src", ramal_numero)

    # Seleciona Tipo: Saínte e Status: Atendida
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
    page.wait_for_selector(".pDiv .pReload:not(.loading)", timeout=20000)
    page.wait_for_timeout(2000)


def processar_chamadas(page, model_whisper):
    registros = []

    for ramal in RAMAIS:
        num = ramal["numero"]
        nome = ramal["nome"]
        print(f"\n==========================================")
        print(f"Consultando Ramal {num} ({nome}) para o dia {DATA_CONSULTA}")
        print(f"==========================================")

        aplicar_filtros(page, num)
        total_paginas = obter_total_paginas(page)
        print(f"[{nome}] Total de páginas: {total_paginas}")

        for pag in range(1, total_paginas + 1):
            if pag > 1:
                mudar_pagina_flexigrid(page, pag)

            try:
                page.wait_for_selector("tr[id^='tr_']", timeout=8000)
                linhas = page.locator("tr[id^='tr_']").all()
            except Exception:
                linhas = []

            print(f"Página {pag}/{total_paginas} - {len(linhas)} chamadas encontradas.")

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
                arquivo_base = f"{num}_{nome}_{data_hora.replace('/', '-').replace(':', '-').replace(' ', '_')}_{pag}_{idx}"
                caminho_gsm = os.path.join(AUDIO_DIR, f"{arquivo_base}.gsm")
                caminho_wav = os.path.join(AUDIO_DIR, f"{arquivo_base}.wav")

                if icone_audio.count() > 0:
                    try:
                        icone_audio.first.click()
                        page.wait_for_timeout(500)
                        btn_salvar = tds[9].locator("div img, img[alt*='Salvar'], img[title*='Salvar']").first

                        with page.expect_download(timeout=15000) as download_info:
                            btn_salvar.
