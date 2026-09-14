from datetime import datetime, timedelta
import math
import os
import re
import subprocess
import time
import pandas as pd
from playwright.sync_api import sync_playwright
import whisper

# ==============================================================================
# CONFIGURAÇÕES E NORMALIZAÇÃO ANTI-ALUCINAÇÃO DO WHISPER
# ==============================================================================
DURACAO_MINIMA_SEGUNDOS = int(os.getenv("DURACAO_MINIMA_SEGUNDOS", "5"))

INITIAL_PROMPT_WHISPER = (
    "Fibrart Tanques e Pias. Atendimento comercial B2B de televendas e representantes. "
    "Produtos em marmofibra: tanques simples, tanques duplos, tanques triplos, pia de fibra, "
    "pias com bojo inox, lavatórios, gabinetes, bacia, bancada, válvulas. "
    "Cores: branco liso, preto aracruz, cinza andorinha, bege bahia. "
    "Termos comerciais e logística: orçamentos, cotação, pedidos, tabela de preços, nota fiscal, "
    "faturamento, boleto, rotas de entrega, frete, prazo, região, Grande BH, interior de Minas Gerais, "
    "Sete Lagoas, Belo Horizonte, Curvelo, Montes Claros, Barbacena, Juiz de Fora."
)

# Substituições fonéticas e correções de termos industriais/comerciais Fibrart
SUBSTITUICOES_FONETICAS = [
    # Mapeamento do nome da empresa corrompido por baixa qualidade de áudio
    (r" (fibracha|pipaste|fibrate|fibrati|fibrac|fibrarte|fibrat|fibra\s*art|fibra\s*arte) ", "Fibrart"),
    # Troca acústica de 'região' por 'feijão'
    (r" (feijão) ", "região"),
    # Produtos e acabamentos
    (r" marmo\s+fibra ", "marmofibra"),
    (r" bojo\s+inóx ", "bojo inox"),
]

# Assinaturas / legendas comuns alucinadas pelo Whisper em silêncio ou ruído contínuo
FILTROS_ALUCINACOES_WHISPER = [
    r"legendas?\s+pela\s+comunidade.*",
    r"subtitles?\s+by.*",
    r"transcrito\s+por.*",
    r"obrigado\s+por\s+assistir.*",
    r"inscreva-se\s+no\s+canal.*",
    r"curta\s+e\s+compartilhe.*",
    r"deixe\s+seu\s+like.*",
    r"assista\s+ao\s+pr[óo]ximo\s+v[íi]deo.*",
]

def converter_duracao_segundos(dur_str):
    """Converte formato HH:MM:SS ou MM:SS para segundos inteiros."""
    try:
        partes = [int(p) for p in dur_str.strip().split(":")]
        if len(partes) == 3:
            return partes[0] * 3600 + partes[1] * 60 + partes[2]
        if len(partes) == 2:
            return partes[0] * 60 + partes[1]
    except Exception:
        pass
    return 0

def limpar_repeticoes_alucinadas(texto):
    """
    Detecta e colapsa loops repetitivos de n-grams gerados pelo Whisper
    quando processa tons de chamada, silencio com estatica ou musica de espera.
    Ex: 'o que e o que e...', 'bom bom bom...', 'com a hipotesia...'
    """
    if not texto:
        return ""
    
    # 1. Colapsa palavras unicas repetidas consecutivas: 'bom bom bom' -> 'bom'
    t = re.sub(r'(\b\w+\b)(?:\s+\1){2,}', r'\1', texto, flags=re.IGNORECASE)
    
    # 2. Divide em sentencas e deduplica sentencas consecutivas identicas
    frases = [f.strip() for f in re.split(r'([.!?\n]+)', t) if f.strip()]
    frases_limpas = []
    for f in frases:
        if re.match(r'^[.!?\n]+$', f):
            if frases_limpas:
                frases_limpas[-1] += f
        else:
            if not frases_limpas or f.lower() != frases_limpas[-1].rstrip('.!?\n ').lower():
                frases_limpas.append(f)
    t_dedup = " ".join(frases_limpas)
    
    # 3. Remove n-grams repetitivos em loop (frases de 1 a 8 palavras repetidas em sequencia)
    palavras = t_dedup.split()
    novas = []
    i = 0
    while i < len(palavras):
        repetiu = False
        for n in range(8, 0, -1):
            if i + 2 * n <= len(palavras):
                g1 = [w.lower().strip('.,!?:;') for w in palavras[i:i+n]]
                g2 = [w.lower().strip('.,!?:;') for w in palavras[i+n:i+2*n]]
                if g1 == g2 and any(len(x) > 1 for x in g1):
                    k = 2
                    while i + (k + 1) * n <= len(palavras):
                        gk = [w.lower().strip('.,!?:;') for w in palavras[i+k*n:i+(k+1)*n]]
                        if gk == g1:
                            k += 1
                        else:
                            break
                    novas.extend(palavras[i:i+n])
                    i += k * n
                    repetiu = True
                    break
        if not repetiu:
            novas.append(palavras[i])
            i += 1
            
    res = " ".join(novas).strip()
    
    # Se o texto resultante for puramente uma frase repetitiva residual de toque telefonico
    res_lower = res.lower().strip('.,!? ')
    if res_lower in ['o que é o que é', 'o que é', 'e o que é', 'bom', 'vai ver a gente', 'alô alô', 'não acolhei pantano', 'acolhei pantano']:
        return '[Áudio sem fala inteligível / Toque de chamada]'
        
    return res

def pos_processar_transcricao(texto):
    """
    Aplica filtros de alucinação do Whisper, desduplica loops de repetição,
    normaliza trocas fonéticas e descarta ruído sem fala útil.
    """
    if not texto:
        return ""
    limpo = texto.strip()

    # Remove frases alucinadas de legendas/YouTube
    for padrao in FILTROS_ALUCINACOES_WHISPER:
        limpo = re.sub(padrao, "", limpo, flags=re.IGNORECASE).strip()

    # Aplica correções de vocabulário e fonética
    for padrao, subst in SUBSTITUICOES_FONETICAS:
        limpo = re.sub(padrao, subst, limpo, flags=re.IGNORECASE)

    # Colapsa repetições em loop geradas por música/toque de telefone
    limpo = limpar_repeticoes_alucinadas(limpo)

    # Verifica se restou conteúdo inteligível
    texto_sem_pontuacao = re.sub(r"[^\w\s]", "", limpo).strip()
    if len(texto_sem_pontuacao) < 2:
        return "[Áudio inaudível / Ruído de fundo]"

    return limpo

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
    {"numero": "2005", "nome": "Julia"}
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
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

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
                if frame.locator("#src").count() > 0 and frame.locator("#src").is_visible():
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
    raise TimeoutError(f"Campo #src não foi encontrado após {timeout}ms na URL {page.url}.")

def obter_linhas_tabela(ctx):
    """
    Identifica as linhas reais de chamadas filtrando pelo padrão de data e hora
    'DD/MM HH:MM' (ex: 11/09 14:25).
    """
    loc = ctx.locator("tr").filter(has_text=re.compile(r"\d{2}/\d{2}\s+\d{2}:\d{2}"))
    qtd = loc.count()
    if qtd > 0:
        log_msg(f"[GRID] {qtd} chamadas confirmadas na tabela.")
        return loc.all()

    log_msg("[GRID] Nenhuma linha de chamada encontrada na tela.")
    return []

def obter_info_paginacao(ctx):
    """
    Inspeciona o DOM de forma ampla para detectar controles de paginacao,
    links numericos (1, 2, 3...), botoes Proxima / Next, ou dados do Flexigrid.
    """
    try:
        dados = ctx.evaluate("""() => {
            const pcontrolInput = document.querySelector('.pcontrol input, input[name="page"], .pDiv .pcontrol input');
            const pcontrolSpan = document.querySelector('.pcontrol span, .pDiv .pcontrol span');
            const pagestat = document.querySelector('.pPageStat, .pDiv .pPageStat');
            const btnNextFlexi = document.querySelector('.pNext, .pDiv .pNext');

            // Varre todos os links e botoes na tela para achar links de paginas
            const allLinks = Array.from(document.querySelectorAll('a, button, input[type="button"], input[type="submit"]'));
            const pageNumbers = [];
            let hasNextLink = false;
            let nextLinkText = '';

            for (const el of allLinks) {
                const t = (el.innerText || el.value || '').trim();
                const h = (el.getAttribute('href') || '') + ' ' + (el.getAttribute('onclick') || '');
                const c = (el.className || '').toLowerCase();

                if (/^\d+$/.test(t)) {
                    pageNumbers.push(parseInt(t, 10));
                }

                const tLower = t.toLowerCase();
                if (t === '>' || t === '>>' || tLower.includes('próx') || tLower.includes('prox') || tLower.includes('next') || tLower.includes('seguinte')) {
                    const isDisabled = c.includes('disabled') || c.includes('pdisabled') || el.hasAttribute('disabled');
                    if (!isDisabled) {
                        hasNextLink = true;
                        nextLinkText = t;
                    }
                }
            }

            // Textos informativos de registros (ex: 'Exibindo 1 a 20 de 45')
            let statText = pagestat ? pagestat.innerText.trim() : '';
            if (!statText) {
                const els = Array.from(document.querySelectorAll('div, td, span, b, p'));
                for (const el of els) {
                    const txt = el.innerText ? el.innerText.trim() : '';
                    if ((txt.toLowerCase().includes('registro') || txt.toLowerCase().includes('página') || txt.toLowerCase().includes('pagina')) && txt.length < 100) {
                        statText = txt;
                        break;
                    }
                }
            }

            return {
                flexiInput: pcontrolInput ? pcontrolInput.value.trim() : '',
                flexiSpan: pcontrolSpan ? pcontrolSpan.innerText.trim() : '',
                pageNumbers: pageNumbers,
                hasNextLink: hasNextLink,
                nextLinkText: nextLinkText,
                hasFlexiNext: btnNextFlexi ? !btnNextFlexi.className.includes('pDisabled') : false,
                statText: statText
            };
        }""")

        # Determina a pagina atual
        pag_atual = 1
        if dados.get("flexiInput") and dados["flexiInput"].isdigit():
            pag_atual = int(dados["flexiInput"])

        # Determina o total de paginas
        total_paginas = 1
        if dados.get("pageNumbers"):
            total_paginas = max(dados["pageNumbers"])
        elif dados.get("flexiSpan"):
            nums = re.findall(r"\d+", dados["flexiSpan"])
            if nums:
                total_paginas = int(nums[-1])
        elif dados.get("statText"):
            nums = re.findall(r"\d+", dados["statText"])
            if len(nums) >= 3:
                ini, fim, tot = int(nums[0]), int(nums[1]), int(nums[2])
                por_pag = (fim - ini + 1) if (fim >= ini and ini > 0) else 20
                if tot > 0 and por_pag > 0:
                    total_paginas = math.ceil(tot / por_pag)

        # Determina se existe proxima pagina
        tem_proxima = dados.get("hasNextLink") or dados.get("hasFlexiNext")
        if not tem_proxima and dados.get("pageNumbers"):
            if any(p > pag_atual for p in dados["pageNumbers"]):
                tem_proxima = True

        log_msg(f"[PAGINACAO] Pagina atual: {pag_atual} | Total estimado: {total_paginas} | Proxima disponivel: {tem_proxima} (Links: {dados.get('pageNumbers')} | Next: '{dados.get('nextLinkText')}' | Info: '{dados.get('statText')}')")
        return {
            "pagina_atual": pag_atual,
            "total_paginas": max(1, total_paginas),
            "tem_proxima": tem_proxima
        }
    except Exception as e:
        log_msg(f"Aviso ao obter informacoes de paginacao: {e}")
        return {"pagina_atual": 1, "total_paginas": 1, "tem_proxima": True}

def aguardar_mudanca_pagina(ctx, proxima, texto_linha_antes, timeout=15):
    inicio = time.time()
    ctx.wait_for_timeout(1000)

    for loader in [".pReload.loading", ".gBlock", ".loading", "#loading", ".spinner"]:
        try:
            if ctx.locator(loader).count() > 0:
                ctx.locator(loader).wait_for(state="detached", timeout=10000)
        except Exception:
            pass

    while time.time() - inicio < timeout:
        linhas = obter_linhas_tabela(ctx)
        texto_novo = linhas[0].inner_text().strip() if linhas else ""

        if texto_linha_antes and texto_novo and texto_novo != texto_linha_antes:
            log_msg(f"-> Sucesso: Nova lista de chamadas carregada para a pagina {proxima} (Linha: {texto_novo[:30]}...). ")
            ctx.wait_for_timeout(1000)
            return True

        inp = ctx.locator(".pcontrol input, input[name='page']").first
        if inp.count() > 0:
            val = inp.input_value().strip()
            if val == str(proxima):
                log_msg(f"-> Sucesso: Indicador de pagina atualizado para {proxima}.")
                ctx.wait_for_timeout(1000)
                return True

        ctx.wait_for_timeout(500)

    log_msg(f"Aviso: Timeout aguardando atualizacao dos dados para a pagina {proxima}.")
    return False

def avancar_proxima_pagina(ctx, pagina_atual):
    """
    Tenta avancar a pagina utilizando multiplas estrategias:
    1. Clicar no link com o numero exato da proxima pagina (ex: <a>2</a>)
    2. Clicar em botoes/links Proxima, Next, >, >>
    3. Preencher input de pagina com Enter (padrao Flexigrid)
    4. Injecao de clique via JavaScript
    """
    proxima = pagina_atual + 1
    log_msg(f"-> Acionando avanco da pagina {pagina_atual} para a pagina {proxima}...")

    linhas_antes = obter_linhas_tabela(ctx)
    texto_linha_antes = linhas_antes[0].inner_text().strip() if linhas_antes else ""

    # Estrategia 1: Clicar no link numerico exato da proxima pagina (ex: <a>2</a>)
    try:
        link_num = ctx.locator(f"a:text-is('{proxima}'), button:text-is('{proxima}')").first
        if link_num.count() > 0 and link_num.is_visible():
            log_msg(f"-> Clicando diretamente no link da pagina '{proxima}'...")
            link_num.click(force=True)
            return aguardar_mudanca_pagina(ctx, proxima, texto_linha_antes)
    except Exception as e:
        log_msg(f"Aviso clique link numerico {proxima}: {e}")

    # Estrategia 2: Clicar no botao/link de 'Proxima', 'Next', '>', '>>'
    seletores_next = [
        "a:has-text('Próxima')", "a:has-text('Proxima')", "a:has-text('Seguinte')",
        "a:has-text('Next')", "button:has-text('Próxima')", "button:has-text('Next')",
        "a:has-text('>')", "a:has-text('>>')", "input[value*='>']",
        ".pDiv .pNext", ".pNext", "div.pButton.pNext"
    ]
    for sel in seletores_next:
        try:
            loc = ctx.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                classes = (loc.get_attribute("class") or "").lower()
                if "disabled" in classes or "pdisabled" in classes:
                    log_msg(f"-> Botao '{sel}' esta desabilitado. Fim das paginas.")
                    return False
                log_msg(f"-> Clicando no elemento de avanco: '{sel}'...")
                loc.click(force=True)
                return aguardar_mudanca_pagina(ctx, proxima, texto_linha_antes)
        except Exception:
            pass

    # Estrategia 3: Preenchimento de input de pagina (se existir)
    try:
        input_pag = ctx.locator(".pDiv .pcontrol input, .pcontrol input, input[name='page'], input[name='pagina']").first
        if input_pag.count() > 0 and input_pag.is_visible():
            log_msg(f"-> Preenchendo input de pagina com '{proxima}' + Enter...")
            input_pag.click(force=True)
            input_pag.fill(str(proxima))
            input_pag.press("Enter")
            return aguardar_mudanca_pagina(ctx, proxima, texto_linha_antes)
    except Exception as e:
        log_msg(f"Aviso preenchimento input pagina: {e}")

    # Estrategia 4: Disparo via JavaScript
    log_msg("-> Tentando disparo de avanco via JavaScript...")
    script_js = """(targetPage) => {
        const all = Array.from(document.querySelectorAll('a, button, input[type="button"]'));
        for (const el of all) {
            const t = (el.innerText || el.value || '').trim();
            if (t === String(targetPage)) {
                el.click();
                return 'clicou_link_' + targetPage;
            }
        }
        for (const el of all) {
            const t = (el.innerText || el.value || '').trim().toLowerCase();
            if (t.includes('próx') || t.includes('prox') || t.includes('next') || t === '>' || t === '>>') {
                const c = (el.className || '').toLowerCase();
                if (!c.includes('disabled') && !c.includes('pdisabled')) {
                    el.click();
                    return 'clicou_proxima';
                }
            }
        }
        return 'nada_encontrado';
    }"""
    res_js = ctx.evaluate(script_js, proxima)
    log_msg(f"-> Resultado disparo JS: {res_js}")

    if res_js != 'nada_encontrado':
        return aguardar_mudanca_pagina(ctx, proxima, texto_linha_antes)

    log_msg("-> Nenhum mecanismo de avanco de pagina foi encontrado na tela.")
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
        campo_usuario = page.locator("input[type='text'], input[name='user'], input[name='login'], #src, #user, #login").first
    else:
        for frame in page.frames:
            if frame.locator("input[type='password']").count() > 0:
                campo_senha = frame.locator("input[type='password']").first
                campo_usuario = frame.locator("input[type='text'], input[name='user'], input[name='login'], #src, #user, #login").first
                ctx_login = frame
                log_msg(f"Formulário de login localizado no frame: {frame.name}")
                break

    if not campo_senha:
        log_msg("Aviso: Campo de senha não localizado.")
        return

    log_msg("Preenchendo credenciais de acesso...")
    campo_usuario.fill(PBX_USER)
    campo_senha.fill(PBX_PASSWORD)

    btn_submit = ctx_login.locator("button[type='submit'], input[type='submit'], #submit, .btn-primary").first
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

    for sel in ["#calldate_day_start", "#calldate_start", "input[name*='date_start']"]:
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

    for h_fim in ctx.locator("select[name*='hour_end'], select[name*='hora_fim'], #calldate_hour_end").all():
        for opt in h_fim.locator("option").all():
            if opt.inner_text().strip() == "23":
                h_fim.select_option(value=opt.get_attribute("value"))
                break

    for m_fim in ctx.locator("select[name*='minute_end'], select[name*='minuto_fim'], #calldate_minute_end").all():
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

        # Tenta expandir o limite de registros do relatório para carregar o máximo possível por página
    try:
        campos_limite = ctx.locator("select[name*='limit'], select[name*='qtd'], select[name*='rows'], select[name*='linhas'], select[name*='registros'], select[name*='display'], select[name='rp'], input[name*='limit']").all()
        for cl in campos_limite:
            if cl.is_visible():
                tag = cl.evaluate("el => el.tagName")
                nome_campo = cl.evaluate("el => el.name || el.id || 'limite'")
                if tag == "SELECT":
                    opts = [opt.get_attribute("value") for opt in cl.locator("option").all()]
                    opts_num = [int(o) for o in opts if o and o.isdigit()]
                    if opts_num:
                        maior = str(max(opts_num))
                        cl.select_option(value=maior)
                        log_msg(f"[CONFIG] Select de limite '{nome_campo}' ajustado para o maximo ({maior}).")
                elif tag == "INPUT":
                    cl.fill("500")
                    log_msg(f"[CONFIG] Input de limite '{nome_campo}' preenchido com 500.")
    except Exception as e:
        log_msg(f"Aviso ao verificar campos de limite: {e}")

    log_msg(f"Disparando consulta (#confirm) para o ramal {ramal_numero} no dia {DATA_CONSULTA}...")
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

            log_msg(f"\n--- [{nome}] Processando Página {pagina_atual} de {total_paginas} ({len(linhas)} chamadas identificadas) ---")

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
                tem_icone = (img_audio.count() > 0 and img_audio.is_visible()) or (link_audio.count() > 0 and link_audio.is_visible())

                transcricao = "Sem gravação"
                data_formatada = data_hora.replace("/", "-").replace(":", "-").replace(" ", "_")
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

                        btn_salvar = coluna_audio.locator("img[title*='alvar'], img[src*='save'], a:has(img[src*='save'])").first
                        try:
                            btn_salvar.wait_for(state="attached", timeout=4000)
                        except Exception:
                            btn_salvar = ctx.locator("img[title*='alvar'], img[src*='save'], a:has(img[src*='save'])").last

                        href = ""
                        try:
                            href = btn_salvar.evaluate("el => (el.closest('a') ? el.closest('a').href : '') || el.src || ''")
                        except Exception:
                            pass

                        if href and (".php" in href or "download" in href or ".gsm" in href or ".wav" in href) and not href.endswith(".gif"):
                            url_download = href if href.startswith("http") else f"{PBX_URL.rstrip('/')}/{href.lstrip('/')}"
                            log_msg(f"[{nome} #{idx}] Baixando áudio via requisição direta: {url_download}")
                            resp = page.request.get(url_download)
                            with open(caminho_gsm, "wb") as f_out:
                                f_out.write(resp.body())
                        else:
                            log_msg(f"[{nome} #{idx}] Disparando download via JS...")
                            with page.expect_download(timeout=15000) as download_info:
                                btn_salvar.evaluate("el => (el.closest('a') ? el.closest('a') : el).click()")
                            download = download_info.value
                            download.save_as(caminho_gsm)

                        if os.path.exists(caminho_gsm) and os.path.getsize(caminho_gsm) > 0:
                            tam = os.path.getsize(caminho_gsm)
                            log_msg(f"[{nome} #{idx}] Áudio GSM salvo ({tam} bytes).")
                            converter_gsm_para_wav(caminho_gsm, caminho_wav)

                            dur_seg = converter_duracao_segundos(duracao)
                            if dur_seg < DURACAO_MINIMA_SEGUNDOS:
                                log_msg(f"[{nome} #{idx}] Áudio com duração muito curta ({duracao} / {dur_seg}s). Descartando transcrição para evitar alucinações.")
                                transcricao = f"[Chamada curta ({duracao}) - áudio insuficiente]"
                            else:
                                log_msg(f"[{nome} #{idx}] Transcrevendo com Whisper (initial_prompt + parâmetros anti-alucinação)...[duracao: {duracao}]")
                                resultado = model_whisper.transcribe(
                                    caminho_wav,
                                    language="pt",
                                    initial_prompt=INITIAL_PROMPT_WHISPER,
                                    temperature=(0.0, 0.2, 0.4, 0.6, 0.8),
                                    condition_on_previous_text=False,
                                    compression_ratio_threshold=2.4,
                                    no_speech_threshold=0.6
                                )
                                texto_bruto = resultado.get("text", "").strip()
                                transcricao = pos_processar_transcricao(texto_bruto)
                                log_msg(f"[{nome} #{idx}] Transcrição finalizada e normalizada com sucesso.")
                        else:
                            log_msg(f"[{nome} #{idx}] Arquivo de áudio baixado vazio.")
                            transcricao = "Gravação com tamanho 0 bytes"

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
                    "Arquivo Áudio": f"{arquivo_base}.wav" if os.path.exists(caminho_wav) else "N/A"
                })

            if len(linhas) == 0:
                log_msg(f"[{nome}] Nenhuma linha encontrada na página {pagina_atual}.")
                break

            # Tenta avançar para a próxima página
            log_msg(f"[{nome}] Tentando avançar da página {pagina_atual} para a página {pagina_atual + 1}...")
            avancou = avancar_proxima_pagina(ctx, pagina_atual)
            if not avancou:
                log_msg(f"[{nome}] Fim da paginação após página {pagina_atual} (não há mais páginas a avançar).")
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
            f.write(f"### Atendimento {r['Operador']} (Ramal {r['Ramal']}) - {r['Data/Hora']}\n")
            f.write(f"- **Destino:** {r['Destino']}\n")
            f.write(f"- **Duração:** {r['Duração']}\n")
            f.write(f"- **Status:** {r['Status']}\n")
            f.write(f"- **Transcrição:**\n> {r['Transcrição']}\n\n---\n")

    log_msg(f"\nRelatórios gerados com sucesso ({len(df)} chamadas):\n- {csv_path}\n- {md_path}")

def main():
    log_msg("Iniciando rotina de gravações e transcrições do PABX...")
    modelo_nome = os.getenv("WHISPER_MODEL", "base")
    log_msg(f"Carregando modelo Whisper '{modelo_nome}'...")
    model_whisper = whisper.load_model(modelo_nome)

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
