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


def buscar_em_todos_frames(page, seletor, timeout_ms=15000):
    """Procura um elemento em todos os frames abertos da página."""
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
        time.sleep(0.5)
    return None


def realizar_login(page):
    print(f"[*] Acessando central PABX em {PBX_URL}...")
    page.goto(PBX_URL, timeout=60000, wait_until="domcontentloaded")
    time.sleep(2)
    
    print(f"[*] URL atual: {page.url}")
    print(f"[*] Título da página: '{page.title()}'")

    alvo = page
    user_input, pass_input = encontrar_campos_login(page)

    if not user_input and len(page.frames) > 1:
        print(f"[*] Procurando campos dentro de {len(page.frames)} frames...")
        for idx, f in enumerate(page.frames):
            u, p = encontrar_campos_login(f)
            if u and p:
                user_input, pass_input = u, p
                alvo = f
                print(f"[+] Campos de login encontrados no Frame #{idx+1} ({f.name})")
                break

    if not user_input or not pass_input:
        raise Exception(f"Campos de login não encontrados na tela. Título: '{page.title()}'")

    print("[*] Preenchendo credenciais...")
    user_input.fill(PBX_USER)
    pass_input.fill(PBX_PASSWORD)

    # Submete o formulário dentro do mesmo frame onde estão os campos
    botao = alvo.locator("button[type='submit'], input[type='submit'], button:has-text('Entrar'), button:has-text('Login'), input[value*='Entrar' i], input[value*='Login' i], a:has-text('Entrar'), a:has-text('Login')").first
    try:
        if botao.count() > 0 and botao.is_visible():
            print("[*] Clicando no botão de login dentro do frame...")
            botao.click(timeout=5000)
        else:
            print("[*] Botão não encontrado visualmente. Enviando com 'Enter' no campo de senha...")
            pass_input.press("Enter")
    except Exception:
        print("[*] Pressionando 'Enter' no campo de senha...")
        pass_input.press("Enter")

    time.sleep(3)
    page.wait_for_load_state("networkidle")
    print(f"[+] Login enviado. Título pós-login: '{page.title()}'")


def navegar_para_registro_ligacoes(page):
    print("[*] Navegando até Relatórios -> Ligações -> Registro de ligações...")
    
    relatorios = buscar_em_todos_frames(page, "text=Relatórios, a:has-text('Relatórios'), span:has-text('Relatórios')")
    if relatorios:
        print("[+] Clicando no menu 'Relatórios'...")
        relatorios.click()
        time.sleep(1)

    ligacoes = buscar_em_todos_frames(page, "text=Ligações, a:has-text('Ligações'), span:has-text('Ligações')")
    if ligacoes:
        print("[+] Clicando no submenu 'Ligações'...")
        ligacoes.click()
        time.sleep(1)

    reg = buscar_em_todos_frames(page, "text=Registro de ligações, a:has-text('Registro de ligações'), text=Registro de Ligações")
    if reg:
        print("[+] Clicando na opção 'Registro de ligações'...")
        reg.click()
        time.sleep(2)
        page.wait_for_load_state("networkidle")


def filtrar_e_baixar_ligacoes(page, ramal_info):
    ramal = ramal_info["ramal"]
    nome = ramal_info["nome"]
    pasta_destino = os.path.join(BASE_DOWNLOAD_DIR, f"{ramal}_{nome}")
    os.makedirs(pasta_destino, exist_ok=True)

    print(f"[*] Filtrando ligações para {nome} (Ramal {ramal}) - Data: {DATA_ONTEM}...")

    campo_data_ini = buscar_em_todos_frames(page, "input[name*='data_ini'], input[name*='inicio'], input[name*='start'], input[id*='data_ini'], input[id*='inicio']")
    if campo_data_ini:
        campo_data_ini.fill(DATA_ONTEM)

    campo_data_fim = buscar_em_todos_frames(page, "input[name*='data_fim'], input[name*='fim'], input[name*='end'], input[id*='data_fim'], input[id*='fim']")
    if campo_data_fim:
        campo_data_fim.fill(DATA_ONTEM)

    campo_origem = buscar_em_todos_frames(page, "input[name*='origem'], input[name*='src'], input[name*='source'], input[id*='origem'], input[id*='src']")
    if campo_origem:
        campo_origem.fill(ramal)

    select_tipo = buscar_em_todos_frames(page, "select[name*='tipo'], select[name*='direction'], select[id*='tipo']")
    if select_tipo:
        try:
            select_tipo.select_option(label="Sainte")
        except Exception:
            try:
                select_tipo.select_option(value="sainte")
            except Exception:
                pass

    btn_filtrar = buscar_em_todos_frames(page, "button:has-text('Filtrar'), input[value*='Filtrar' i], button:has-text('Buscar'), input[value*='Buscar' i], button[type='submit'], input[type='submit']")
    if btn_filtrar:
        print("[*] Clicando no botão Filtrar...")
        btn_filtrar.click()
    
    time.sleep(3)
    page.wait_for_load_state("networkidle")

    # Localizar os links de gravação em todos os frames ativos
    links_gravacao = []
    for f in [page] + page.frames:
        try:
            achados = f.locator("a[href*='download'], a[href*='.wav'], a[href*='.mp3'], button[title*='Gravação'], a:has(i.fa-download), a[href*='audio'], img[src*='download'], img[src*='play'], a:has(img[src*='download']), a:has(img[src*='play'])").all()
            for l in achados:
                if l.is_visible():
                    links_gravacao.append(l)
        except Exception:
            pass

    print(f"[*] Gravações encontradas: {len(links_gravacao)}")
    arquivos_baixados = []

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
