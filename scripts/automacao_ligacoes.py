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

# Formato exato da tela: 08/09/2026
DATA_ONTEM_BR = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
DATA_DIR = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
BASE_DOWNLOAD_DIR = os.path.join(os.getcwd(), "ligacoes", DATA_DIR)


def encontrar_campos_login(escopo):
    seletores_user = [
        "input[name*='user']", "input[name*='login']", "input[name*='usuario']",
        "input[id*='user']", "input[id*='login']", "input[id*='usuario']",
        "input:not([type='hidden']):not([type='password']):not([type='submit']):not([type='checkbox']):not([type='radio'])"
    ]
    seletores_pass = [
        "input[type='password']", "input[name*='pass']", "input[name*='senha']",
        "input[id*='pass']", "input[id*='senha']"
    ]

    user_field, pass_field = None, None
    for sel in seletores_user:
        loc = escopo.locator(sel).first
        try:
            if loc.count() > 0 and loc.is_visible():
                user_field = loc
                break
        except Exception:
            pass

    for sel in seletores_pass:
        loc = escopo.locator(sel).first
        try:
            if loc.count() > 0 and loc.is_visible():
                pass_field = loc
                break
        except Exception:
            pass

    return user_field, pass_field


def buscar_em_todos_frames(page, seletor, timeout_ms=8000):
    inicio = time.time()
    while (time.time() - inicio) < (timeout_ms / 1000):
        try:
            loc = page.locator(seletor).first
            if loc.count() > 0 and loc.is_visible():
                return loc
        except Exception:
            pass

        for f in page.frames:
            try:
                loc = f.locator(seletor).first
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                pass
        time.sleep(0.4)
    return None


def realizar_login(page):
    print(f"[*] Acessando central PABX em {PBX_URL}...")
    page.goto(PBX_URL, timeout=60000, wait_until="domcontentloaded")
    time.sleep(2)

    alvo = page
    user_input, pass_input = encontrar_campos_login(page)

    if not user_input and len(page.frames) > 1:
        for idx, f in enumerate(page.frames):
            u, p = encontrar_campos_login(f)
            if u and p:
                user_input, pass_input = u, p
                alvo = f
                break

    if not user_input or not pass_input:
        raise Exception(f"Campos de login não encontrados. Título: '{page.title()}'")

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
    print(f"[+] Login concluído com sucesso.")


def navegar_para_registro_ligacoes(page):
    print("[*] Navegando até Relatórios -> Ligações -> Registro de ligações...")
    
    relatorios = buscar_em_todos_frames(page, "text=/^Relat[oó]rios/i, a:has-text('Relatórios'), a:has-text('Relatorios'), span:has-text('Relatórios')")
    if relatorios:
        relatorios.click()
        time.sleep(1)

    ligacoes = buscar_em_todos_frames(page, "text=/^Liga[cç][oõ]es/i, a:has-text('Ligações'), a:has-text('Ligacoes')")
    if ligacoes:
        ligacoes.click()
        time.sleep(1)

    reg = buscar_em_todos_frames(page, "text=/Registro de liga[cç][oõ]es/i, a:has-text('Registro de ligações'), a:has-text('Registro de Ligacoes')")
    if reg:
        reg.click()
        time.sleep(3)
        page.wait_for_load_state("networkidle")


def converter_gsm_para_wav(caminho_gsm):
    """Converte áudio .gsm para .wav usando FFmpeg."""
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
        print(f"    [!] Erro ao converter GSM para WAV: {e}")
        return caminho_gsm


def filtrar_e_baixar_ligacoes(page, ramal_info):
    ramal = ramal_info["ramal"]
    nome = ramal_info["nome"]
    pasta_destino = os.path.join(BASE_DOWNLOAD_DIR, f"{ramal}_{nome}")
    os.makedirs(pasta_destino, exist_ok=True)

    print(f"\n[*] =================== PROCESSANDO RAMAL {ramal} ({nome}) ===================")

    # 1. Preenchimento de Data: De e Até
    # Procura campos de texto ou data no frame
    campo_de = buscar_em_todos_frames(page, "input[name*='data_ini'], input[name*='de'], input[id*='data_ini'], input[id*='de']")
    if campo_de:
        print(f"[*] Preenchendo data 'De': {DATA_ONTEM_BR}")
        campo_de.fill(DATA_ONTEM_BR)

    campo_ate = buscar_em_todos_frames(page, "input[name*='data_fim'], input[name*='ate'], input[id*='data_fim'], input[id*='ate']")
    if campo_ate:
        print(f"[*] Preenchendo data 'Até': {DATA_ONTEM_BR}")
        campo_ate.fill(DATA_ONTEM_BR)

    # 2. Preenchimento de Origem (Ramal)
    campo_origem = buscar_em_todos_frames(page, "input[name*='origem'], input[name*='src'], input[id*='origem'], input[id*='src']")
    if campo_origem:
        print(f"[*] Preenchendo Origem: {ramal}")
        campo_origem.fill(ramal)

    # 3. Seleção do Tipo: Saínte (com e sem acento)
    select_tipo = buscar_em_todos_frames(page, "select[name*='tipo'], select[id*='tipo']")
    if select_tipo:
        try:
            select_tipo.select_option(label="Saínte")
            print("[*] Tipo selecionado: 'Saínte'")
        except Exception:
            try:
                select_tipo.select_option(label="Sainte")
            except Exception:
                try:
                    select_tipo.select_option(value="sainte")
                except Exception:
                    pass

    # 4. Clicar no botão Consultar (conforme a imagem)
    btn_consultar = buscar_em_todos_frames(page, "button:has-text('Consultar'), input[value*='Consultar' i], a:has-text('Consultar')")
    if btn_consultar:
        print("[*] Clicando no botão 'Consultar'...")
        btn_consultar.click()
    else:
        print("[!] Botão 'Consultar' não encontrado diretamente, tentando envio padrão...")

    time.sleep(4)
    page.wait_for_load_state("networkidle")

    # 5. Coleta e download de gravações por página
    arquivos_processados = []
    pagina_atual = 1
    max_paginas = 8

    while pagina_atual <= max_paginas:
        print(f"[*] Verificando gravações na página {pagina_atual}...")
        
        # Encontra o frame que contém a tabela de chamadas
        frame_tabela = page
        for f in page.frames:
            if f.locator("table").count() > 0:
                frame_tabela = f
                break

        # Linhas de chamadas da tabela
        linhas = frame_tabela.locator("table tr").all()
        print(f"[*] Total de linhas na tabela da página {pagina_atual}: {len(linhas)}")

        for idx, row in enumerate(linhas):
            try:
                # O ícone de gravação (nota musical ♫) fica na última coluna
                ultimo_td = row.locator("td").last
                if ultimo_td.count() == 0:
                    continue

                # Verifica se há link ou elemento clicável de áudio na última coluna
                elemento_audio = ultimo_td.locator("a, img, i, button, span").first
                if elemento_audio.count() > 0 and elemento_audio.is_visible():
                    # Extrai dados da linha
                    colunas = [td.inner_text().strip() for td in row.locator("td").all()]
                    data_hora = colunas[0] if len(colunas) > 0 else ""
                    duracao = colunas if len(colunas) > 1 else ""
                    destino = colunas if len(colunas) > 3 else ""
                    status = colunas if len(colunas) > 4 else ""

                    print(f"    [+] Chamada com gravação encontrada: {data_hora} | Destino: {destino} | Duração: {duracao}")

                    # Dispara o download do arquivo GSM
                    with page.expect_download(timeout=15000) as download_info:
                        elemento_audio.click()
                    download = download_info.value
                    
                    nome_original = download.suggested_filename
                    nome_gsm = f"ligacao_{ramal}_{pagina_atual}_{idx}_{nome_original}"
                    caminho_gsm = os.path.join(pasta_destino, nome_gsm)
                    download.save_as(caminho_gsm)
                    print(f"        -> Arquivo GSM salvo: {nome_gsm}")

                    # Converte de .gsm para .wav para transcrição e reprodução
                    caminho_wav = converter_gsm_para_wav(caminho_gsm)

                    arquivos_processados.append({
                        "arquivo_gsm": caminho_gsm,
                        "arquivo_wav": caminho_wav,
                        "data_hora": data_hora,
                        "duracao": duracao,
                        "destino": destino,
                        "status": status
                    })
            except Exception as row_err:
                pass

        # Paginação: tenta avançar para a próxima página clicando no botão '>'
        btn_proximo = frame_tabela.locator("a:has-text('>'), button:has-text('>'), input[value='>']").first
        try:
            if btn_proximo.count() > 0 and btn_proximo.is_visible() and btn_proximo.is_enabled():
                print("[*] Avançando para a próxima página de chamadas...")
                btn_proximo.click()
                time.sleep(3)
                page.wait_for_load_state("networkidle")
                pagina_atual += 1
            else:
                break
        except Exception:
            break

    print(f"[*] Total de gravações baixadas para {nome} (Ramal {ramal}): {len(arquivos_processados)}")
    return arquivos_processados


def transcrever_chamadas(dados_chamadas):
    if not dados_chamadas:
        return []

    print("[*] Carregando modelo Whisper para transcrição dos áudios...")
    model = whisper.load_model("base")
    resultados = []

    for item in dados_chamadas:
        audio_path = item["arquivo_wav"] if os.path.exists(item["arquivo_wav"]) else item["arquivo_gsm"]
        print(f"[*] Transcrevendo chamada ({item['data_hora']} - {item['destino']})...")
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

    print(f"[+] Relatório gerado com sucesso em: {relatorio_md_path}")


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
