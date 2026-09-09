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

RAMAIS = [
    {"ramal": "2003", "nome": "Fernanda"},
    {"ramal": "2005", "nome": "Julia"}
]

DATA_ONTEM_BR = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
DATA_DIR = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
BASE_DOWNLOAD_DIR = os.path.join(os.getcwd(), "ligacoes", DATA_DIR)


def obter_frame_relatorio(page):
    """Retorna o frame que possui o campo #src ou #confirm."""
    for f in page.frames:
        try:
            if f.locator("#src").count() > 0 or f.locator("#confirm").count() > 0:
                return f
        except Exception:
            pass
    return page


def realizar_login(page):
    print(f"[*] Acessando central PABX em {PBX_URL}...")
    page.goto(PBX_URL, timeout=60000, wait_until="networkidle")
    time.sleep(3)

    # 1. Verifica se já está logado e com a tela aberta (#src presente)
    for f in page.frames:
        try:
            if f.locator("#src").count() > 0 and f.locator("#src").is_visible():
                print("[+] Tela de relatório (#src) já está aberta. Pulando login.")
                return
        except Exception:
            pass

    # 2. Aguarda até 12 segundos para os inputs de login carregarem no frame
    alvo = None
    user_input = None
    pass_input = None

    print("[*] Aguardando carregamento dos formulários de login...")
    for _ in range(12):
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
        print("[!] Campos de login não encontrados. Verificando se já está autenticado...")
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
    print("[+] Login efetuado com sucesso.")


def navegar_para_registro_ligacoes(page):
    print("[*] Verificando acesso à tela de Registro de Ligações...")
    
    # Se #src já estiver visível, já estamos no relatório
    for _ in range(3):
        frame = obter_frame_relatorio(page)
        if frame.locator("#src").count() > 0 and frame.locator("#src").is_visible():
            print("[+] Tela de Registro de Ligações pronta.")
            return
        time.sleep(1)

    # Tentativa 1: Atalho direto na página inicial
    for f in page.frames:
        atalho = f.locator("text=/REGISTROS DE LIGA[CÇ][OÕ]ES/i, a:has-text('REGISTROS DE LIGAÇÕES'), a:has-text('Registros de ligações')").first
        try:
            if atalho.count() > 0 and atalho.is_visible():
                print("[+] Clicando no atalho direto 'REGISTROS DE LIGAÇÕES'...")
                atalho.click()
                time.sleep(3)
                page.wait_for_load_state("networkidle")
                return
        except Exception:
            pass

    # Tentativa 2: Menu lateral
    print("[*] Navegando via menu lateral...")
    for f in page.frames:
        rel = f.locator("text=/^Relat[oó]rios/i, a:has-text('Relatórios'), span:has-text('Relatórios')").first
        try:
            if rel.count() > 0 and rel.is_visible():
                rel.click()
                time.sleep(1)
                break
        except Exception:
            pass

    for f in page.frames:
        lig = f.locator("text=/^Liga[cç][oõ]es/i, a:has-text('Ligações')").first
        try:
            if lig.count() > 0 and lig.is_visible():
                lig.click()
                time.sleep(1)
                break
        except Exception:
            pass

    for f in page.frames:
        reg = f.locator("text=/Registro de liga[cç][oõ]es/i, a:has-text('Registro de ligações')").first
        try:
            if reg.count() > 0 and reg.is_visible():
                reg.click()
                time.sleep(3)
                page.wait_for_load_state("networkidle")
                break
        except Exception:
            pass


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


def filtrar_e_baixar_ligacoes(page, ramal_info):
    ramal = ramal_info["ramal"]
    nome = ramal_info["nome"]
    pasta_destino = os.path.join(BASE_DOWNLOAD_DIR, f"{ramal}_{nome}")
    os.makedirs(pasta_destino, exist_ok=True)

    print(f"\n[*] =================== PROCESSANDO RAMAL {ramal} ({nome}) ===================")
    frame = obter_frame_relatorio(page)

    # 1. Ajusta campos de data para a data de ontem (padrão DD/MM/AAAA)
    for inp in frame.locator("input[type='text'], input:not([type])").all():
        try:
            val = inp.input_value()
            if re.match(r"^\d{2}/\d{2}/\d{4}$", val):
                print(f"[*] Ajustando data ({val}) para {DATA_ONTEM_BR}...")
                inp.fill(DATA_ONTEM_BR)
        except Exception:
            pass

    # 2. Preenche o campo #src (Origem) conforme gravado no DevTools
    campo_origem = frame.locator("#src")
    if campo_origem.count() > 0:
        print(f"[*] Preenchendo Origem (#src): {ramal}")
        campo_origem.fill(ramal)

    # 3. Seleciona Tipo 'Saínte'
    select_tipo = frame.locator("select").first
    if select_tipo.count() > 0:
        try:
            select_tipo.select_option(label="Saínte")
            print("[*] Tipo selecionado: 'Saínte'")
        except Exception:
            try:
                select_tipo.select_option(label="Sainte")
            except Exception:
                pass

    # 4. Clica no botão #confirm (Consultar) conforme gravado no DevTools
    btn_confirm = frame.locator("#confirm")
    if btn_confirm.count() > 0:
        print("[*] Clicando no botão Consultar (#confirm)...")
        btn_confirm.click()

    time.sleep(5)
    page.wait_for_load_state("networkidle")
    frame = obter_frame_relatorio(page)

    # 5. Coleta as chamadas com gravação por página
    chamadas_baixadas = []
    pagina_atual = 1
    max_paginas = 6

    while pagina_atual <= max_paginas:
        print(f"[*] Verificando chamadas na página {pagina_atual}...")
        linhas = frame.locator("tr[id^='tr_']").all()
        print(f"[*] Total de linhas encontradas na página {pagina_atual}: {len(linhas)}")

        for idx, row in enumerate(linhas):
            try:
                # Ícone de nota musical na 10ª coluna conforme DevTools: td:nth-of-type(10) > a > img
                icone_audio = row.locator("td:nth-of-type(10) > a > img, td:nth-child(10) > a > img").first

                if icone_audio.count() > 0 and icone_audio.is_visible():
                    tds = [td.inner_text().strip() for td in row.locator("td").all()]
                    data_hora = tds[0] if len(tds) > 0 else ""
                    duracao = tds if len(tds) > 1 else ""
                    destino = tds if len(tds) > 3 else ""
                    status = tds if len(tds) > 4 else ""

                    print(f"    [+] Gravação detectada: {data_hora} | Destino: {destino} | Duração: {duracao}")

                    # 1. Clica na nota musical para abrir a caixinha
                    icone_audio.click()
                    time.sleep(1)

                    # 2. Clica no botão Salvar conforme o DevTools: td:nth-of-type(10) > div img
                    btn_salvar = row.locator("td:nth-of-type(10) > div img, td:nth-child(10) > div img, img[alt*='Salvar' i], a:has(img[alt*='Salvar' i])").first
                    
                    with page.expect_download(timeout=15000) as download_info:
                        if btn_salvar.count() > 0 and btn_salvar.is_visible():
                            btn_salvar.click()
                        else:
                            frame.locator("img[alt*='Salvar' i], a:has-text('Salvar')").first.click()

                    download = download_info.value
                    nome_original = download.suggested_filename
                    nome_gsm = f"ligacao_{ramal}_p{pagina_atual}_{idx}_{nome_original}"
                    caminho_gsm = os.path.join(pasta_destino, nome_gsm)
                    download.save_as(caminho_gsm)
                    print(f"        -> Arquivo GSM salvo: {nome_gsm}")

                    # Converte para WAV
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

        # Paginação: avança para a próxima página no botão '>'
        btn_proximo = frame.locator("a:has-text('>'), button:has-text('>'), input[value='>']").first
        try:
            if btn_proximo.count() > 0 and btn_proximo.is_visible() and btn_proximo.is_enabled():
                print("[*] Avançando para a próxima página de chamadas...")
                btn_proximo.click()
                time.sleep(3)
                page.wait_for_load_state("networkidle")
                frame = obter_frame_relatorio(page)
                pagina_atual += 1
            else:
                break
        except Exception:
            break

    print(f"[*] Total de gravações baixadas para {nome} (Ramal {ramal}): {len(chamadas_baixadas)}")
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
        f.write(f"# Relatório de Ligações Saintes Gravadas - {DATA_ONTEM_BR}\n\n")

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
            navegar_para_registro_ligacoes(page)

            for ramal_info in RAMAIS:
                chamadas_baixadas = filtrar_e_baixar_ligacoes(page, ramal_info)
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
