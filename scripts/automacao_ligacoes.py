import os
import re
import time
import subprocess
from datetime import datetime, timedelta
import pandas as pd
from playwright.sync_api import sync_playwright
import whisper

def extrair_url(valor_env):
    if not valor_env:
        return "http://177.10.116.84/"
    limpo = re.sub(r"\s+", "", valor_env)
    match_ip = re.search(r"(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?", limpo)
    if match_ip:
        return f"http://{match_ip.group(0)}/"
    dominio = re.sub(r"^(https?://)+", "", limpo, flags=re.IGNORECASE).strip("/'\"")
    if dominio and not dominio.startswith("*") and "." in dominio:
        return f"http://{dominio}/"
    return "http://177.10.116.84/"

def limpar_credencial(chave, padrao):
    val = os.getenv(chave, padrao)
    if not val or val.strip() == "" or val.strip().startswith("***"):
        return padrao
    s = val.strip()
    if "=" in s:
        s = s.split("=", 1).strip()
    elif ":" in s and s.lower().startswith(chave.lower()):
        s = s.split(":", 1).strip()
    return s.strip("\"' ")

PBX_URL = extrair_url(os.getenv("PBX_URL", "177.10.116.84"))
PBX_USER = limpar_credencial("PBX_USER", "lucas")
PBX_PASSWORD = limpar_credencial("PBX_PASSWORD", "lcsu251535")

URL_REGISTROS_DIRETO = f"{PBX_URL}pbxip/framework/container.php?token=MAIN/cmVwb3J0LmNhbGxzLmRldGFpbGVk"

RAMAIS = [
    {"ramal": "2003", "nome": "Fernanda"},
    {"ramal": "2005", "nome": "Julia"}
]

DATA_ONTEM_BR = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
DATA_DIR = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
BASE_DOWNLOAD_DIR = os.path.join(os.getcwd(), "ligacoes", DATA_DIR)


def realizar_login(page):
    print(f"[*] Acessando {PBX_URL} para autenticação...")
    page.goto(PBX_URL, timeout=60000, wait_until="networkidle")
    time.sleep(2)

    alvo = None
    user_input = None
    pass_input = None

    for _ in range(10):
        for f in [page] + page.frames:
            p = f.locator("input[type='password'], input[name*='pass'], input[id*='pass']").first
            u = f.locator("input[name*='user'], input[name*='login'], input[id*='user'], input:not([type='password']):not([type='hidden'])").first
            try:
                if p.count() > 0 and p.is_visible():
                    user_input, pass_input = u, p
                    alvo = f
                    break
            except Exception:
                pass
        if user_input and pass_input:
            break
        time.sleep(1)

    if not user_input or not pass_input:
        print("[!] Formulário de login não visível (sessão já ativa).")
        return

    print("[*] Preenchendo credenciais...")
    user_input.fill(PBX_USER)
    pass_input.fill(PBX_PASSWORD)

    botao = alvo.locator("button[type='submit'], input[type='submit'], button:has-text('Entrar'), button:has-text('Login')").first
    try:
        if botao.count() > 0 and botao.is_visible():
            botao.click(timeout=4000)
        else:
            pass_input.press("Enter")
    except Exception:
        pass_input.press("Enter")

    time.sleep(4)
    page.wait_for_load_state("networkidle")
    print("[+] Autenticação finalizada.")


def acessar_tela_registros(page):
    print(f"[*] Acessando módulo de registros direto: {URL_REGISTROS_DIRETO}...")
    page.goto(URL_REGISTROS_DIRETO, timeout=60000, wait_until="networkidle")
    time.sleep(3)

    if page.locator("#src").count() > 0 or page.locator("#confirm").count() > 0:
        return page

    for f in page.frames:
        if f.locator("#src").count() > 0 or f.locator("#confirm").count() > 0:
            return f

    return page


def converter_gsm_para_wav(caminho_gsm):
    caminho_wav = caminho_gsm.rsplit(".", 1)[0] + ".wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", caminho_gsm, caminho_wav],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        return caminho_wav
    except Exception as e:
        print(f"    [!] Erro ao converter {caminho_gsm} para WAV: {e}")
        return caminho_gsm


def detectar_total_paginas(escopo):
    total = 1
    elementos = escopo.locator(".pcontrol, .pDiv, .pGroup, span:has-text('/'), div:has-text('/')").all()
    for el in elementos:
        try:
            texto = el.inner_text().strip()
            match = re.search(r"/\s*(\d+)", texto)
            if match:
                total = max(total, int(match.group(1)))
                break
        except Exception:
            pass
    return total


def avancar_pagina_flexigrid(page, escopo, proxima_pagina):
    """Avança para a próxima página no Flexigrid acionando os eventos jQuery nativos."""
    print(f"[*] Solicitando mudança para a página {proxima_pagina}...")

    candidatos = [escopo, page] + [f for f in page.frames if f != escopo and f != page]

    for idx_c, c in enumerate(candidatos):
        try:
            res_js = c.evaluate(f"""() => {{
                // 1. Aciona via jQuery diretamente nos botões do Flexigrid
                if (window.jQuery) {{
                    let jNext = window.jQuery('.pNext, .pButton.pNext, div.pNext');
                    if (jNext.length && !jNext.hasClass('pDisable')) {{
                        jNext.trigger('click');
                        return 'jQuery .pNext clicado';
                    }}

                    // Dispara pelo input de página via jQuery
                    let jInput = window.jQuery('.pcontrol input, input[name="page"]');
                    if (jInput.length) {{
                        jInput.val('{proxima_pagina}').trigger('change');
                        let e = window.jQuery.Event('keydown');
                        e.keyCode = 13;
                        e.which = 13;
                        jInput.trigger(e);
                        return 'jQuery input enter disparado para {proxima_pagina}';
                    }}
                }}

                // 2. Busca estritamente dentro de document.body pelo container do Flexigrid
                let pDiv = document.body.querySelector('.pDiv, .pDiv2');
                if (pDiv) {{
                    let btn = pDiv.querySelector('.pNext, .pButton.pNext');
                    if (btn) {{
                        btn.click();
                        return 'DOM pDiv pNext clicado';
                    }}
                    let allBtns = Array.from(pDiv.querySelectorAll('.pButton, div[class*="btn"], div[class*="Button"]'));
                    if (allBtns.length >= 4) {{
                        allBtns.click();
                        return 'pDiv allBtns clicado';
                    }}
                }}

                // 3. Clique direto no botão HTML nativo
                let btnHtml = document.body.querySelector('.pNext, .pButton.pNext, div.pNext, [class*="pNext"]');
                if (btnHtml) {{
                    btnHtml.click();
                    return 'DOM direto pNext clicado';
                }}

                return 'nao encontrado';
            }}""")

            if res_js != 'nao encontrado':
                print(f"    [+] Ação de paginação executada no frame #{idx_c}: {res_js}")
                time.sleep(5)
                page.wait_for_load_state("networkidle")
                return True
        except Exception:
            pass

    # Fallback via Playwright com force=True
    for c in candidatos:
        try:
            btn = c.locator(".pNext, .pButton.pNext, div.pNext, [class*='pNext']").first
            if btn.count() > 0:
                btn.click(force=True)
                time.sleep(5)
                page.wait_for_load_state("networkidle")
                return True
        except Exception:
            pass

    print(f"    [!] Não foi possível avançar para a página {proxima_pagina}.")
    return False


def filtrar_e_baixar_ligacoes(page, escopo, ramal_info):
    ramal = ramal_info["ramal"]
    nome = ramal_info["nome"]
    pasta_destino = os.path.join(BASE_DOWNLOAD_DIR, f"{ramal}_{nome}")
    os.makedirs(pasta_destino, exist_ok=True)

    print(f"\n[*] =================== PROCESSANDO RAMAL {ramal} ({nome}) ===================")

    # 1. Atualizar datas para o dia anterior
    for inp in escopo.locator("input[type='text'], input:not([type])").all():
        try:
            val = inp.input_value()
            if re.match(r"^\d{2}/\d{2}/\d{4}$", val):
                print(f"[*] Atualizando data ({val}) para {DATA_ONTEM_BR}...")
                inp.fill(DATA_ONTEM_BR)
        except Exception:
            pass

    # 2. Preencher Origem (#src)
    campo_origem = escopo.locator("#src")
    if campo_origem.count() > 0:
        print(f"[*] Preenchendo Origem (#src): {ramal}")
        campo_origem.fill(ramal)

    # 3. Selecionar Tipo 'Saínte' e Status 'Atendida'
    selects = escopo.locator("select").all()
    for sel in selects:
        try:
            opts = sel.inner_text()
            if "Atendida" in opts:
                sel.select_option(label="Atendida")
                print("[*] Status selecionado: 'Atendida'")
            elif "Saínte" in opts or "Sainte" in opts:
                try:
                    sel.select_option(label="Saínte")
                except Exception:
                    sel.select_option(label="Sainte")
                print("[*] Tipo selecionado: 'Saínte'")
        except Exception:
            pass

    # 4. Clicar no botão Consultar (#confirm)
    btn_confirm = escopo.locator("#confirm")
    if btn_confirm.count() > 0:
        print("[*] Clicando no botão Consultar (#confirm)...")
        btn_confirm.click()

    time.sleep(5)
    page.wait_for_load_state("networkidle")

    # 5. Detecta o total de páginas geradas
    total_paginas = detectar_total_paginas(escopo)
    print(f"[+] Total de páginas com ligações Atendidas: {total_paginas}")

    chamadas_baixadas = []

    for pagina_atual in range(1, total_paginas + 1):
        print(f"\n[*] --- Processando Página {pagina_atual} de {total_paginas} ---")

        # Linhas da tabela
        linhas = escopo.locator("tr[id^='tr_']").all()
        if len(linhas) == 0:
            linhas = [r for r in escopo.locator("table tr").all() if r.locator("td").count() >= 8]

        print(f"[*] Total de linhas encontradas na página {pagina_atual}: {len(linhas)}")

        for idx, row in enumerate(linhas):
            try:
                # Ícone de nota musical na 10ª coluna
                icone_audio = row.locator("td:nth-of-type(10) > a > img, td:nth-child(10) > a > img, td:last-child a > img").first

                if icone_audio.count() > 0 and icone_audio.is_visible():
                    colunas = [td.inner_text().strip() for td in row.locator("td").all()]

                    # Mapeamento correto com os dados reais
                    data_hora = colunas if len(colunas) > 1 and colunas else (colunas[0] if colunas else "")
                    duracao = colunas if len(colunas) > 2 else ""
                    origem_num = colunas if len(colunas) > 3 else ""
                    destino = colunas if len(colunas) > 4 else ""
                    status = colunas if len(colunas) > 5 else ""

                    print(f"    [+] Gravação detectada (Pág {pagina_atual}): {data_hora} | Destino: {destino} | Duração: {duracao}")

                    # 1. Clica na nota musical
                    icone_audio.click()
                    time.sleep(1)

                    # 2. Clica no botão Salvar que surge na célula
                    btn_salvar = row.locator("td:nth-of-type(10) > div img, td:nth-child(10) > div img, img[alt*='Salvar' i], div img").first

                    with page.expect_download(timeout=15000) as download_info:
                        if btn_salvar.count() > 0 and btn_salvar.is_visible():
                            btn_salvar.click()
                        else:
                            escopo.locator("img[alt*='Salvar' i], a:has-text('Salvar')").first.click()

                    download = download_info.value
                    nome_original = download.suggested_filename
                    nome_gsm = f"ligacao_{ramal}_p{pagina_atual}_{idx}_{nome_original}"
                    caminho_gsm = os.path.join(pasta_destino, nome_gsm)
                    download.save_as(caminho_gsm)
                    print(f"        -> GSM salvo: {nome_gsm}")

                    # Converte de .gsm para .wav
                    caminho_wav = converter_gsm_para_wav(caminho_gsm)

                    chamadas_baixadas.append({
                        "arquivo_gsm": caminho_gsm,
                        "arquivo_wav": caminho_wav,
                        "data_hora": data_hora,
                        "duracao": duracao,
                        "destino": destino,
                        "status": status
                    })
            except Exception as e:
                pass

        # 6. Avança para a próxima página se houver mais páginas
        if pagina_atual < total_paginas:
            proxima = pagina_atual + 1
            sucesso = avancar_pagina_flexigrid(page, escopo, proxima)
            if not sucesso:
                print(f"[!] Encerrando paginação na página {pagina_atual}.")
                break

    print(f"\n[*] Total consolidado de gravações baixadas para {nome} (Ramal {ramal}): {len(chamadas_baixadas)}")
    return chamadas_baixadas


def transcrever_chamadas(dados_chamadas):
    if not dados_chamadas:
        return []

    print("[*] Carregando modelo Whisper para transcrição dos áudios...")
    model = whisper.load_model("base")
    resultados = []

    for item in dados_chamadas:
        audio_path = item["arquivo_wav"] if os.path.exists(item["arquivo_wav"]) else item["arquivo_gsm"]
        print(f"[*] Transcrevendo chamada ({item['data_hora']} -> {item['destino']})...")
        try:
            res = model.transcribe(audio_path, language="pt")
            texto = res.get("text", "").strip()
            item["transcricao"] = texto
        except Exception as e:
            item["transcricao"] = f"[Erro na transcrição: {e}]"

        resultados.append(item)

    return resultados


def gerar_relatorio(dados_por_ramal):
    relatorio_md_path = os.path.join(BASE_DOWNLOAD_DIR, f"relatorio_ligacoes_{DATA_DIR}.md")
    linhas_csv = []

    with open(relatorio_md_path, "w", encoding="utf-8") as f:
        f.write(f"# Relatório de Ligações Saintes Atendidas - {DATA_ONTEM_BR}\n\n")

        for item in dados_por_ramal:
            ramal = item["ramal"]
            nome = item["nome"]
            chamadas = item["chamadas"]

            f.write(f"## Atendente: {nome} (Ramal {ramal})\n\n")
            f.write(f"**Total de gravações processadas:** {len(chamadas)}\n\n")

            if not chamadas:
                f.write("*Nenhuma chamada gravada disponível para este ramal na data.*\n\n")
                continue

            for idx, lig in enumerate(chamadas, 1):
                f.write(f"### Chamada #{idx}\n")
                f.write(f"- **Data/Hora:** {lig.get('data_hora', '-')}\n")
                f.write(f"- **Duração:** {lig.get('duracao', '-')}\n")
                f.write(f"- **Número Destino:** {lig.get('destino', '-')}\n")
                f.write(f"- **Status:** {lig.get('status', '-')}\n")
                f.write(f"- **Arquivo:** `{os.path.basename(lig.get('arquivo_wav', ''))}`\n\n")
                f.write(f"**Transcrição do Áudio:**\n> {lig.get('transcricao', '[Sem transcrição]')}\n\n---\n\n")

                linhas_csv.append({
                    "Data": DATA_ONTEM_BR,
                    "Ramal": ramal,
                    "Atendente": nome,
                    "Data_Hora": lig.get("data_hora", ""),
                    "Duracao": lig.get("duracao", ""),
                    "Destino": lig.get("destino", ""),
                    "Status": lig.get("status", ""),
                    "Arquivo_WAV": os.path.basename(lig.get("arquivo_wav", "")),
                    "Transcricao": lig.get("transcricao", "")
                })

    if linhas_csv:
        df = pd.DataFrame(linhas_csv)
        csv_path = os.path.join(BASE_DOWNLOAD_DIR, f"relatorio_ligacoes_{DATA_DIR}.csv")
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"[+] Relatório final gerado em: {relatorio_md_path}")


def main():
    os.makedirs(BASE_DOWNLOAD_DIR, exist_ok=True)
    dados_consolidados = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = browser.new_context(
            accept_downloads=True,
            http_credentials={"username": PBX_USER, "password": PBX_PASSWORD}
        )
        page = context.new_page()

        try:
            realizar_login(page)
            escopo = acessar_tela_registros(page)

            for ramal_info in RAMAIS:
                chamadas_baixadas = filtrar_e_baixar_ligacoes(page, escopo, ramal_info)
                chamadas_transcritas = transcrever_chamadas(chamadas_baixadas)
                dados_consolidados.append({
                    "ramal": ramal_info["ramal"],
                    "nome": ramal_info["nome"],
                    "chamadas": chamadas_transcritas
                })

            gerar_relatorio(dados_consolidados)

        except Exception as e:
            print(f"[!] Falha durante a execução: {e}")
            try:
                page.screenshot(path=os.path.join(BASE_DOWNLOAD_DIR, "screenshot_erro.png"))
            except Exception:
                pass
            raise e

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
