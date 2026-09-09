import os
import re
import time
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

DATA_ONTEM = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
DATA_DIR = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
BASE_DOWNLOAD_DIR = os.path.join(os.getcwd(), "ligacoes", DATA_DIR)


def encontrar_campos_login(escopo):
    """Varre um frame ou página buscando os campos de usuário e senha."""
    seletores_user = [
        "input[name*='user']", "input[name*='login']", "input[name*='usuario']",
        "input[id*='user']", "input[id*='login']", "input[id*='usuario']",
        "input[placeholder*='usuário' i]", "input[placeholder*='login' i]",
        "input:not([type='hidden']):not([type='password']):not([type='submit']):not([type='checkbox']):not([type='radio'])"
    ]
    seletores_pass = [
        "input[type='password']", "input[name*='pass']", "input[name*='senha']",
        "input[id*='pass']", "input[id*='senha']"
    ]

    user_field = None
    pass_field = None

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


def realizar_login(page):
    print(f"[*] Acessando central PABX em {PBX_URL}...")
    page.goto(PBX_URL, timeout=60000, wait_until="domcontentloaded")
    time.sleep(2)
    
    print(f"[*] URL atual: {page.url}")
    print(f"[*] Título da página: '{page.title()}'")

    # Mapeia os inputs visíveis para diagnóstico
    todos_inputs = page.locator("input").all()
    print(f"[*] Total de inputs encontrados na página principal: {len(todos_inputs)}")
    for i, inp in enumerate(todos_inputs):
        try:
            n = inp.get_attribute("name") or ""
            i_id = inp.get_attribute("id") or ""
            t = inp.get_attribute("type") or "text"
            print(f"    Input #{i+1}: name='{n}', id='{i_id}', type='{t}'")
        except Exception:
            pass

    # Tenta localizar campos na página principal
    user_input, pass_input = encontrar_campos_login(page)

    # Se não encontrou, verifica se a tela está dentro de um frame/iframe
    if not user_input and len(page.frames) > 1:
        print(f"[*] Procurando campos dentro de {len(page.frames)} frames/iframes...")
        for idx, f in enumerate(page.frames):
            print(f"    Frame #{idx+1}: name='{f.name}', url='{f.url}'")
            u, p = encontrar_campos_login(f)
            if u and p:
                user_input, pass_input = u, p
                print(f"[+] Campos de login encontrados dentro do Frame #{idx+1}")
                break

    if not user_input or not pass_input:
        raise Exception(f"Não foi possível identificar os campos de login na página. Título: '{page.title()}', URL: '{page.url}'")

    print("[*] Preenchendo credenciais...")
    user_input.fill(PBX_USER)
    pass_input.fill(PBX_PASSWORD)

    botao = page.locator("button[type='submit'], input[type='submit'], button:has-text('Entrar'), button:has-text('Login'), input[value*='Entrar' i], input[value*='Login' i]").first
    botao.click()
    page.wait_for_load_state("networkidle")
    print("[+] Formulário de login enviado com sucesso.")


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
        # Suporte a HTTP Basic Auth e downloads
        context = browser.new_context(
            accept_downloads=True,
            http_credentials={"username": PBX_USER, "password": PBX_PASSWORD}
        )
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

        except Exception as e:
            print(f"[!] Falha durante a execução: {e}")
            try:
                caminho_screenshot = os.path.join(BASE_DOWNLOAD_DIR, "screenshot_erro.png")
                page.screenshot(path=caminho_screenshot)
                print(f"[*] Screenshot do estado atual salvo em: {caminho_screenshot}")
            except Exception as ss_err:
                print(f"[-] Não foi possível tirar screenshot: {ss_err}")
            raise e

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
