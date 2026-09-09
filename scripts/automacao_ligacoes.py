import os
import time
from datetime import datetime, timedelta
import pandas as pd
from playwright.sync_api import sync_playwright
import whisper

# Sanitização e padronização das variáveis de ambiente
raw_url = os.getenv("PBX_URL", "http://177.10.116.84/").strip().strip('"\'')
if not raw_url.startswith(("http://", "https://")):
    raw_url = f"http://{raw_url}"
if not raw_url.endswith("/"):
    raw_url = f"{raw_url}/"

PBX_URL = raw_url
PBX_USER = os.getenv("PBX_USER", "lucas").strip().strip('"\'')
PBX_PASSWORD = os.getenv("PBX_PASSWORD", "lcsu251535").strip().strip('"\'')

RAMAIS = [
    {"ramal": "2003", "nome": "Fernanda"},
    {"ramal": "2005", "nome": "Julia"}
]

DATA_ONTEM = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
DATA_DIR = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
BASE_DOWNLOAD_DIR = os.path.join(os.getcwd(), "ligacoes", DATA_DIR)


def realizar_login(page):
    print(f"[*] Acessando {PBX_URL}...")
    page.goto(PBX_URL, timeout=60000)
    
    user_input = page.locator("input[name*='user'], input[name*='login'], input[type='text']").first
    pass_input = page.locator("input[type='password']").first
    
    user_input.fill(PBX_USER)
    pass_input.fill(PBX_PASSWORD)
    
    page.locator("button[type='submit'], input[type='submit'], button:has-text('Entrar'), button:has-text('Login')").first.click()
    page.wait_for_load_state("networkidle")
    print("[+] Login realizado com sucesso.")


def navegar_para_registro_ligacoes(page):
    print("[*] Navegando até Relatórios -> Ligações -> Registro de ligações...")
    menu_relatorios = page.locator("text=Relatórios, a:has-text('Relatórios'), span:has-text('Relatórios')").first
    menu_relatorios.click()
    time.sleep(1)

    submenu_ligacoes = page.locator("text=Ligações, a:has-text('Ligações'), span:has-text('Ligações')").first
    submenu_ligacoes.click()
    time.sleep(1)

    opcao_registro = page.locator("text=Registro de ligações, a:has-text('Registro de ligações')").first
    opcao_registro.click()
    page.wait_for_load_state("networkidle")


def filtrar_e_baixar_ligacoes(page, ramal_info):
    ramal = ramal_info["ramal"]
    nome = ramal_info["nome"]
    pasta_destino = os.path.join(BASE_DOWNLOAD_DIR, f"{ramal}_{nome}")
    os.makedirs(pasta_destino, exist_ok=True)

    print(f"[*] Filtrando ligações para {nome} (Ramal {ramal}) - Data: {DATA_ONTEM}...")

    campo_data_ini = page.locator("input[name*='data_ini'], input[name*='inicio'], input[name*='start']").first
    campo_data_fim = page.locator("input[name*='data_fim'], input[name*='fim'], input[name*='end']").first
    
    if campo_data_ini.is_visible():
        campo_data_ini.fill(DATA_ONTEM)
    if campo_data_fim.is_visible():
        campo_data_fim.fill(DATA_ONTEM)

    campo_origem = page.locator("input[name*='origem'], input[name*='src'], input[name*='source']").first
    if campo_origem.is_visible():
        campo_origem.fill(ramal)

    select_tipo = page.locator("select[name*='tipo'], select[name*='direction']").first
    if select_tipo.is_visible():
        try:
            select_tipo.select_option(label="Sainte")
        except Exception:
            try:
                select_tipo.select_option(value="sainte")
            except Exception:
                pass

    page.locator("button:has-text('Filtrar'), input[value='Filtrar'], button:has-text('Buscar'), input[value='Buscar']").first.click()
    page.wait_for_load_state("networkidle")
    time.sleep(3)

    links_gravacao = page.locator("a[href*='download'], a[href*='.wav'], a[href*='.mp3'], button[title*='Gravação'], a:has(i.fa-download)").all()

    arquivos_baixados = []
    print(f"[*] Gravações encontradas: {len(links_gravacao)}")

    for idx, link in enumerate(links_gravacao):
        try:
            with page.expect_download(timeout=15000) as download_info:
                link.click()
            download = download_info.value
            nome_arquivo = f"ligacao_{ramal}_{idx + 1}_{download.suggested_filename}"
            caminho_final = os.path.join(pasta_destino, nome_arquivo)
            download.save_as(caminho_final)
            arquivos_baixados.append(caminho_final)
            print(f"    [+] Baixado: {nome_arquivo}")
        except Exception as err:
            print(f"    [-] Falha ao baixar item {idx + 1}: {err}")

    return arquivos_baixados


def transcrever_audios(arquivos_audio):
    if not arquivos_audio:
        return []

    print("[*] Inicializando Whisper (modelo base)...")
    model = whisper.load_model("base")
    resultados = []

    for arq in arquivos_audio:
        print(f"[*] Transcrevendo {os.path.basename(arq)}...")
        try:
            res = model.transcribe(arq, language="pt")
            texto = res.get("text", "").strip()
            resultados.append({
                "arquivo": os.path.basename(arq),
                "caminho": arq,
                "transcricao": texto
            })
        except Exception as e:
            resultados.append({
                "arquivo": os.path.basename(arq),
                "caminho": arq,
                "transcricao": f"[Erro: {e}]"
            })

    return resultados


def gerar_relatorio(dados_por_ramal):
    relatorio_md_path = os.path.join(BASE_DOWNLOAD_DIR, f"relatorio_ligacoes_{DATA_DIR}.md")
    linhas_tabela = []

    with open(relatorio_md_path, "w", encoding="utf-8") as f:
        f.write(f"# Relatório de Ligações Saintes - {DATA_ONTEM}\n\n")
        
        for item in dados_por_ramal:
            ramal = item["ramal"]
            nome = item["nome"]
            f.write(f"## Atendente: {nome} (Ramal {ramal})\n\n")
            
            if not item["transcricoes"]:
                f.write("*Nenhuma ligação com gravação encontrada.*\n\n")
                continue

            for idx, lig in enumerate(item["transcricoes"], 1):
                f.write(f"### Chamada #{idx} - `{lig['arquivo']}`\n")
                f.write(f"**Transcrição:**\n> {lig['transcricao']}\n\n")

                linhas_tabela.append({
                    "Data": DATA_ONTEM,
                    "Ramal": ramal,
                    "Atendente": nome,
                    "Arquivo": lig["arquivo"],
                    "Transcrição": lig["transcricao"]
                })

    if linhas_tabela:
        df = pd.DataFrame(linhas_tabela)
        csv_path = os.path.join(BASE_DOWNLOAD_DIR, f"relatorio_ligacoes_{DATA_DIR}.csv")
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"[+] Relatório consolidado salvo em: {relatorio_md_path}")


def main():
    os.makedirs(BASE_DOWNLOAD_DIR, exist_ok=True)
    dados_consolidados = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        try:
            realizar_login(page)
            navegar_para_registro_ligacoes(page)

            for ramal_info in RAMAIS:
                arquivos = filtrar_e_baixar_ligacoes(page, ramal_info)
                transcricoes = transcrever_audios(arquivos)
                dados_consolidados.append({
                    "ramal": ramal_info["ramal"],
                    "nome": ramal_info["nome"],
                    "transcricoes": transcricoes
                })

            gerar_relatorio(dados_consolidados)

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
