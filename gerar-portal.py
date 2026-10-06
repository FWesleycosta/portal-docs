#!/usr/bin/env python3
"""Gera um portal de documentação em uma única página HTML a partir do Markdown do repositório.

A fonte da verdade continua sendo o Markdown em docs/. O HTML é derivado e nunca deve ser editado à mão.

Uso:
  gerar-portal.py [--repo .] [--saida docs/portal.html] [--incluir 'modules/*/README.md'] [--online]

Por padrão a biblioteca do Mermaid é embutida (a página funciona sem internet) quando for encontrada
na instalação do mermaid-cli; com --online, ou se não for encontrada, ela é carregada de um CDN.
"""
import argparse
import base64
import datetime
import glob
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import unicodedata

try:
    import markdown
except ImportError:
    sys.exit("erro: falta a biblioteca markdown. Rode o gerar-portal.sh, que prepara o ambiente.")

CATEGORIAS = [
    ("visao-geral", "Visão geral"),
    ("arquitetura", "Arquitetura"),
    ("negocio", "Regras de negócio"),
    ("dados", "Dados"),
    ("pipelines", "Pipelines"),
    ("runbooks", "Runbooks"),
    ("decisoes", "Decisões"),
    ("modulos", "Módulos"),
    ("outros", "Outros"),
]
NOME_CATEGORIA = dict(CATEGORIAS)
ORDEM_CATEGORIA = {c: i for i, (c, _) in enumerate(CATEGORIAS)}
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"
ALERTAS = {"NOTE": "Observação", "TIP": "Dica", "IMPORTANT": "Importante", "WARNING": "Atenção", "CAUTION": "Cuidado"}


def git(repo, *args):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return ""


def slug_github(texto):
    """Âncora no estilo do GitHub: minúsculas, sem pontuação, espaços viram hífen, acentos mantidos."""
    texto = unicodedata.normalize("NFC", texto.strip().lower())
    texto = re.sub(r"[^\w\- ]", "", texto, flags=re.UNICODE)
    return texto.replace(" ", "-")


def id_documento(caminho_rel):
    base = re.sub(r"\.md$", "", caminho_rel, flags=re.I)
    base = re.sub(r"(^|/)README$", r"\1indice", base, flags=re.I)
    return "doc-" + re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


def categoria(caminho_rel):
    partes = caminho_rel.split("/")
    if caminho_rel == "README.md":
        return "visao-geral"
    if partes[0] == "modules":
        return "modulos"
    if partes[0] == "docs" and len(partes) > 2 and partes[1] in NOME_CATEGORIA:
        return partes[1]
    if caminho_rel == "docs/README.md":
        return "visao-geral"
    return "outros"


def titulo_do_markdown(texto, padrao):
    m = re.search(r"^#\s+(.+?)\s*$", texto, flags=re.M)
    return m.group(1).strip() if m else padrao


def coletar(repo, incluir):
    arquivos = []
    for padrao in ["README.md", "docs/**/*.md"] + incluir:
        for caminho in sorted(glob.glob(os.path.join(repo, padrao), recursive=True)):
            rel = os.path.relpath(caminho, repo).replace(os.sep, "/")
            if rel.endswith("portal.html") or rel in arquivos:
                continue
            arquivos.append(rel)
    return arquivos


def imagem_embutida(caminho):
    tipo = mimetypes.guess_type(caminho)[0] or "application/octet-stream"
    with open(caminho, "rb") as f:
        return f"data:{tipo};base64," + base64.b64encode(f.read()).decode()


def link_arquivo(repo, destino, contexto):
    """Arquivo do repositório que não está no portal: link para o GitHub, ou caminho relativo à página gerada."""
    if contexto["github"]:
        return f'{contexto["github"]}/{destino}'
    return os.path.relpath(os.path.join(repo, destino), os.path.dirname(contexto["saida"])).replace(os.sep, "/")


def processar(repo, rel, docs_por_caminho, pendencias, avisos, contexto):
    with open(os.path.join(repo, rel), encoding="utf-8") as f:
        texto = f.read()
    doc_id = docs_por_caminho[rel]["id"]
    pasta = os.path.dirname(rel)

    def slugify(valor, separador):
        return f"{doc_id}--{slug_github(valor)}"

    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "toc", "attr_list", "md_in_html"],
                           extension_configs={"toc": {"slugify": slugify, "permalink": False}})
    corpo = md.convert(texto)

    # Mermaid: o bloco vira <pre class="mermaid"> para ser desenhado na página
    corpo = re.sub(r'<pre><code class="language-mermaid">(.*?)</code></pre>',
                   r'<pre class="mermaid">\1</pre>', corpo, flags=re.S)

    # Alertas do GitHub: > [!NOTE] ...
    def alerta(m):
        tipo = m.group(1).upper()
        return f'<div class="alerta alerta-{tipo.lower()}"><p class="alerta-titulo">{ALERTAS[tipo]}</p><p>'
    corpo = re.sub(r"<blockquote>\s*<p>\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*", alerta, corpo)
    corpo = re.sub(r'(<div class="alerta[^"]*">.*?)</blockquote>', r"\1</div>", corpo, flags=re.S)

    # Imagens locais: embutidas para a página funcionar sozinha
    def img(m):
        antes, src, depois = m.group(1), html.unescape(m.group(2)), m.group(3)
        if re.match(r"^(https?:|data:)", src):
            return m.group(0)
        caminho = os.path.normpath(os.path.join(repo, pasta, src.split("#")[0]))
        if not os.path.isfile(caminho):
            avisos.append(f"{rel}: imagem não encontrada: {src}")
            return m.group(0)
        return f'{antes}{imagem_embutida(caminho)}{depois}'
    corpo = re.sub(r'(<img[^>]*?src=")([^"]+)("[^>]*>)', img, corpo)

    # Links entre documentos viram navegação interna
    def link(m):
        href = html.unescape(m.group(2))
        if re.match(r"^(https?:|mailto:|data:)", href):
            return f'{m.group(1)}{m.group(2)}" target="_blank" rel="noopener'
        if href.startswith("#"):
            return f'{m.group(1)}#{doc_id}--{slug_github(href[1:])}'
        alvo, _, frag = href.partition("#")
        destino = os.path.normpath(os.path.join(pasta, alvo)).replace(os.sep, "/") if alvo else rel
        if destino in docs_por_caminho:
            alvo_id = docs_por_caminho[destino]["id"]
            return f'{m.group(1)}#{alvo_id}' + (f"--{slug_github(frag)}" if frag else "")
        if os.path.exists(os.path.join(repo, destino)):
            url = link_arquivo(repo, destino, contexto) + (f"#{frag}" if frag else "")
            return f'{m.group(1)}{html.escape(url)}" target="_blank" rel="noopener" title="Arquivo do repositório: {html.escape(destino)}'
        avisos.append(f"{rel}: link para arquivo inexistente: {href}")
        return m.group(0)
    corpo = re.sub(r'(<a[^>]*?href=")([^"]+)', link, corpo)

    # Pendências [A CONFIRMAR: ...]
    contador = [0]
    def pendencia(m):
        contador[0] += 1
        pid = f"{doc_id}--pendencia-{contador[0]}"
        texto_p = re.sub(r"<[^>]+>", "", m.group(0)).strip("[]")
        texto_p = re.sub(r"^A CONFIRMAR:?\s*", "", texto_p)
        pendencias.append({"id": pid, "doc": doc_id, "titulo": docs_por_caminho[rel]["titulo"], "texto": texto_p})
        return f'<mark class="pendencia" id="{pid}">{m.group(0)}</mark>'
    corpo = re.sub(r"\[A CONFIRMAR[^\]]*\]", pendencia, corpo)

    return corpo, md.toc_tokens


def montar(repo, saida, incluir, online):
    repo = os.path.abspath(repo)
    arquivos = coletar(repo, incluir)
    if not arquivos:
        sys.exit("erro: nenhum Markdown encontrado (README.md ou docs/).")

    docs = {}
    for rel in arquivos:
        with open(os.path.join(repo, rel), encoding="utf-8") as f:
            texto = f.read()
        padrao = os.path.basename(os.path.dirname(rel)) if rel.lower().endswith("readme.md") else os.path.splitext(os.path.basename(rel))[0]
        docs[rel] = {"id": id_documento(rel), "titulo": titulo_do_markdown(texto, padrao or "Início"), "categoria": categoria(rel), "rel": rel}

    nome_repo = git(repo, "remote", "get-url", "origin")
    github = None
    if "github.com" in nome_repo:
        nome_repo = re.sub(r"^.*github\.com[:/]([^/]+/[^/]+?)(\.git)?$", r"\1", nome_repo)
        github = f"https://github.com/{nome_repo}/blob/{git(repo, 'rev-parse', '--abbrev-ref', 'HEAD') or 'main'}"
    elif nome_repo:
        nome_repo = re.sub(r"^.*[:/]([^/]+/[^/]+?)(\.git)?$", r"\1", nome_repo)
    else:
        nome_repo = os.path.basename(repo)
    contexto = {"github": github, "saida": os.path.join(repo, saida)}

    pendencias, avisos, secoes = [], [], []
    for rel in arquivos:
        corpo, toc = processar(repo, rel, docs, pendencias, avisos, contexto)
        docs[rel]["toc"] = [{"id": t["id"], "nome": t["name"]} for t in (toc[0]["children"] if len(toc) == 1 else toc)]
        secoes.append(f'<section class="doc" id="{docs[rel]["id"]}" data-titulo="{html.escape(docs[rel]["titulo"])}" hidden>'
                      f'<p class="origem">{html.escape(rel)}</p>{corpo}</section>')

    # Seção de pendências reunidas
    if pendencias:
        itens = "".join(f'<li><a href="#{p["id"]}">{html.escape(p["titulo"])}</a>: {html.escape(p["texto"])}</li>' for p in pendencias)
        corpo_p = f"<h1>Pendências</h1><p>Itens marcados como [A CONFIRMAR] em todos os documentos: são perguntas para o time.</p><ol>{itens}</ol>"
    else:
        corpo_p = "<h1>Pendências</h1><p>Nenhuma pendência [A CONFIRMAR] nos documentos.</p>"
    secoes.append(f'<section class="doc" id="doc-pendencias" data-titulo="Pendências" hidden>{corpo_p}</section>')

    ordem = sorted(docs.values(), key=lambda d: (ORDEM_CATEGORIA[d["categoria"]], d["rel"] != "README.md", d["rel"]))
    nav = []
    for cat, nome in CATEGORIAS:
        itens = [d for d in ordem if d["categoria"] == cat]
        if itens:
            nav.append({"categoria": nome, "docs": [{"id": d["id"], "titulo": d["titulo"], "toc": d["toc"]} for d in itens]})
    nav.append({"categoria": "Pendências", "docs": [{"id": "doc-pendencias", "titulo": f"Pendências ({len(pendencias)})", "toc": []}]})

    mermaid_js = None
    if not online:
        try:
            raiz_npm = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
            for candidato in [os.environ.get("MERMAID_JS", ""),
                              os.path.join(raiz_npm, "@mermaid-js/mermaid-cli/node_modules/mermaid/dist/mermaid.min.js"),
                              os.path.join(raiz_npm, "mermaid/dist/mermaid.min.js")]:
                if os.path.isfile(candidato):
                    with open(candidato, encoding="utf-8") as f:
                        mermaid_js = f.read()
                    break
        except Exception:
            pass
        if not mermaid_js:
            avisos.append("mermaid.min.js local não encontrado; usando o CDN (os diagramas Mermaid precisam de internet).")
    if mermaid_js:
        script_mermaid = "<script>" + mermaid_js.replace("</script", "<\\/script") + "</script>"
    else:
        script_mermaid = f'<script src="{MERMAID_CDN}"></script>'

    commit = git(repo, "rev-parse", "--short", "HEAD") or "sem commit"
    saida_rel = os.path.relpath(os.path.join(repo, saida), repo).replace(os.sep, "/")
    alterado = " (com alterações não commitadas)" if git(repo, "status", "--porcelain", "--", "README.md", "docs", f":!{saida_rel}") else ""
    gerado = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    pagina = MODELO.replace("{{TITULO}}", html.escape(f"Documentação: {nome_repo}")) \
        .replace("{{REPO}}", html.escape(nome_repo)) \
        .replace("{{RODAPE}}", html.escape(f"Gerado em {gerado} a partir do commit {commit}{alterado}. Fonte: Markdown do repositório; não edite este arquivo.")) \
        .replace("{{NAV}}", json.dumps(nav, ensure_ascii=False).replace("</", "<\\/")) \
        .replace("{{INICIAL}}", ordem[0]["id"]) \
        .replace("{{SECOES}}", "\n".join(secoes)) \
        .replace("{{MERMAID}}", script_mermaid)

    destino = os.path.join(repo, saida)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        f.write(pagina)

    print(f"Portal gerado: {saida} ({os.path.getsize(destino) // 1024} KB, {len(arquivos)} documentos, {len(pendencias)} pendências)")
    for a in avisos:
        print(f"aviso: {a}")


MODELO = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITULO}}</title>
<style>
:root {
  --fundo: #ffffff; --painel: #f6f7f9; --texto: #1d2330; --suave: #5b6475; --borda: #dfe3ea;
  --destaque: #1061b0; --destaque-suave: #e7f0fa; --codigo: #f1f3f6; --marca: #fff1c2;
  --nota: #1061b0; --dica: #1a7f37; --importante: #8250df; --atencao: #9a6700; --cuidado: #cf222e;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-tema="claro"]) {
    --fundo: #12161d; --painel: #1a1f28; --texto: #e6e9ef; --suave: #9aa4b5; --borda: #2c3442;
    --destaque: #6cb0f5; --destaque-suave: #1c2a3b; --codigo: #1f2531; --marca: #4a3d10;
    --nota: #6cb0f5; --dica: #4ac26b; --importante: #b392f0; --atencao: #d4a72c; --cuidado: #ff7b72;
  }
}
:root[data-tema="escuro"] {
  --fundo: #12161d; --painel: #1a1f28; --texto: #e6e9ef; --suave: #9aa4b5; --borda: #2c3442;
  --destaque: #6cb0f5; --destaque-suave: #1c2a3b; --codigo: #1f2531; --marca: #4a3d10;
  --nota: #6cb0f5; --dica: #4ac26b; --importante: #b392f0; --atencao: #d4a72c; --cuidado: #ff7b72;
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--fundo); color: var(--texto); }
body { font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: grid; grid-template-columns: 300px 1fr; min-height: 100vh; }
aside { background: var(--painel); border-right: 1px solid var(--borda); position: sticky; top: 0; height: 100vh; overflow-y: auto; padding: 16px; }
.repo { font-weight: 700; font-size: 16px; margin: 0 0 12px; word-break: break-all; }
#busca { width: 100%; padding: 8px 10px; border: 1px solid var(--borda); border-radius: 6px; background: var(--fundo); color: var(--texto); font: inherit; }
.categoria { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--suave); margin: 18px 0 4px; }
nav a { display: block; color: var(--texto); text-decoration: none; padding: 4px 8px; border-radius: 4px; }
nav a:hover { background: var(--destaque-suave); }
nav a.ativo { background: var(--destaque-suave); color: var(--destaque); font-weight: 600; }
nav .toc a { font-size: 13px; color: var(--suave); padding-left: 20px; }
#resultados a { display: block; padding: 6px 8px; border-radius: 4px; color: var(--texto); text-decoration: none; }
#resultados a:hover { background: var(--destaque-suave); }
#resultados small { display: block; color: var(--suave); }
.tema { margin-top: 20px; background: none; border: 1px solid var(--borda); color: var(--suave); border-radius: 6px; padding: 4px 10px; cursor: pointer; font: inherit; font-size: 13px; }
main { padding: 32px 48px 64px; max-width: 1100px; width: 100%; }
.origem { font-size: 12px; color: var(--suave); margin: 0 0 8px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
h1 { font-size: 28px; margin: 0 0 16px; } h2 { font-size: 21px; margin-top: 36px; padding-bottom: 4px; border-bottom: 1px solid var(--borda); } h3 { font-size: 17px; margin-top: 28px; }
a { color: var(--destaque); }
code { background: var(--codigo); padding: 1px 5px; border-radius: 4px; font-size: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
pre { background: var(--codigo); padding: 12px 14px; border-radius: 6px; overflow-x: auto; } pre code { padding: 0; background: none; }
pre.mermaid { background: #ffffff; text-align: center; border: 1px solid var(--borda); }
img { max-width: 100%; height: auto; background: #ffffff; border: 1px solid var(--borda); border-radius: 6px; cursor: zoom-in; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--borda); padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: var(--painel); cursor: pointer; user-select: none; white-space: nowrap; } th::after { content: " ↕"; color: var(--suave); font-size: 11px; }
.filtro-tabela { padding: 6px 10px; border: 1px solid var(--borda); border-radius: 6px; background: var(--fundo); color: var(--texto); font: inherit; font-size: 13px; width: 280px; max-width: 100%; }
.alerta { border-left: 4px solid var(--nota); background: var(--painel); padding: 8px 14px; margin: 14px 0; border-radius: 0 6px 6px 0; }
.alerta p { margin: 6px 0; } .alerta-titulo { font-weight: 700; }
.alerta-note { border-color: var(--nota); } .alerta-note .alerta-titulo { color: var(--nota); }
.alerta-tip { border-color: var(--dica); } .alerta-tip .alerta-titulo { color: var(--dica); }
.alerta-important { border-color: var(--importante); } .alerta-important .alerta-titulo { color: var(--importante); }
.alerta-warning { border-color: var(--atencao); } .alerta-warning .alerta-titulo { color: var(--atencao); }
.alerta-caution { border-color: var(--cuidado); } .alerta-caution .alerta-titulo { color: var(--cuidado); }
blockquote { border-left: 4px solid var(--borda); margin: 14px 0; padding: 2px 14px; color: var(--suave); }
mark.pendencia { background: var(--marca); color: inherit; padding: 1px 4px; border-radius: 3px; }
mark.busca { background: var(--marca); color: inherit; }
footer { margin-top: 48px; padding-top: 12px; border-top: 1px solid var(--borda); font-size: 12px; color: var(--suave); }
#zoom { position: fixed; inset: 0; background: rgba(0,0,0,.8); display: none; align-items: center; justify-content: center; z-index: 10; cursor: zoom-out; padding: 24px; }
#zoom img { max-width: 100%; max-height: 100%; cursor: zoom-out; }
#menu { display: none; }
@media (max-width: 860px) {
  body { grid-template-columns: 1fr; }
  aside { position: fixed; inset: 0 30% 0 0; z-index: 5; transform: translateX(-100%); transition: transform .2s; }
  aside.aberto { transform: none; }
  main { padding: 64px 16px 48px; }
  #menu { display: block; position: fixed; top: 12px; left: 12px; z-index: 6; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--borda); background: var(--painel); color: var(--texto); font: inherit; }
}
@media print { aside, #menu { display: none; } body { display: block; } .doc { display: block !important; page-break-after: always; } }
</style>
</head>
<body>
<button id="menu" type="button">Menu</button>
<aside>
  <p class="repo">{{REPO}}</p>
  <input id="busca" type="search" placeholder="Buscar em toda a documentação" aria-label="Buscar">
  <div id="resultados"></div>
  <nav id="nav" aria-label="Documentos"></nav>
  <button class="tema" type="button" id="tema">Alternar tema</button>
</aside>
<main>
{{SECOES}}
<footer>{{RODAPE}}</footer>
</main>
<div id="zoom" role="dialog" aria-label="Imagem ampliada"><img alt=""></div>
{{MERMAID}}
<script>
const NAV = {{NAV}};
const INICIAL = "{{INICIAL}}";
const nav = document.getElementById("nav");
const docs = [...document.querySelectorAll(".doc")];

function el(tag, attrs, texto) { const e = document.createElement(tag); Object.assign(e, attrs || {}); if (texto) e.textContent = texto; return e; }
NAV.forEach(grupo => {
  nav.appendChild(el("div", { className: "categoria" }, grupo.categoria));
  grupo.docs.forEach(d => {
    nav.appendChild(el("a", { href: "#" + d.id, className: "doc-link" }, d.titulo)).dataset.doc = d.id;
    if (d.toc.length) {
      const toc = el("div", { className: "toc" }); toc.dataset.doc = d.id; toc.hidden = true;
      d.toc.forEach(t => toc.appendChild(el("a", { href: "#" + t.id }, t.nome)));
      nav.appendChild(toc);
    }
  });
});

let mermaidPronto = false;
async function desenhar(secao) {
  if (!window.mermaid) return;
  if (!mermaidPronto) { mermaid.initialize({ startOnLoad: false, theme: "default", securityLevel: "strict" }); mermaidPronto = true; }
  const blocos = [...secao.querySelectorAll("pre.mermaid:not([data-processed])")];
  if (blocos.length) { try { await mermaid.run({ nodes: blocos }); } catch (e) { console.warn(e); } }
}

function mostrar(hash) {
  const alvo = hash ? document.getElementById(decodeURIComponent(hash.slice(1))) : null;
  const secao = alvo ? (alvo.classList.contains("doc") ? alvo : alvo.closest(".doc")) : document.getElementById(INICIAL);
  if (!secao) return;
  docs.forEach(d => d.hidden = d !== secao);
  document.querySelectorAll(".doc-link").forEach(a => a.classList.toggle("ativo", a.dataset.doc === secao.id));
  document.querySelectorAll(".toc").forEach(t => t.hidden = t.dataset.doc !== secao.id);
  document.title = secao.dataset.titulo + " · {{REPO}}";
  desenhar(secao);
  if (alvo && alvo !== secao) alvo.scrollIntoView(); else window.scrollTo(0, 0);
  document.querySelector("aside").classList.remove("aberto");
}
window.addEventListener("hashchange", () => mostrar(location.hash));

// Tabelas: ordenação por coluna e filtro nas maiores
document.querySelectorAll("main table").forEach(tabela => {
  const corpo = tabela.tBodies[0]; if (!corpo) return;
  const linhas = () => [...corpo.rows];
  [...tabela.querySelectorAll("th")].forEach((th, i) => {
    let asc = true;
    th.title = "Ordenar por esta coluna";
    th.addEventListener("click", () => {
      const ordenadas = linhas().sort((a, b) => a.cells[i].textContent.localeCompare(b.cells[i].textContent, "pt-BR", { numeric: true }) * (asc ? 1 : -1));
      ordenadas.forEach(l => corpo.appendChild(l)); asc = !asc;
    });
  });
  if (linhas().length >= 6) {
    const filtro = el("input", { type: "search", className: "filtro-tabela", placeholder: "Filtrar esta tabela" });
    filtro.addEventListener("input", () => { const t = filtro.value.toLowerCase(); linhas().forEach(l => l.hidden = !l.textContent.toLowerCase().includes(t)); });
    tabela.before(filtro);
  }
});

// Busca em todos os documentos
const busca = document.getElementById("busca"), resultados = document.getElementById("resultados");
busca.addEventListener("input", () => {
  const termo = busca.value.trim().toLowerCase();
  resultados.innerHTML = ""; nav.hidden = termo.length > 1;
  if (termo.length < 2) return;
  docs.forEach(d => {
    d.querySelectorAll("h1, h2, h3, p, li, td").forEach(bloco => {
      const texto = bloco.textContent; const pos = texto.toLowerCase().indexOf(termo);
      if (pos < 0 || resultados.childElementCount > 40) return;
      const ancora = bloco.id ? bloco : (bloco.closest("[id]") || d);
      const titulo = bloco.closest(".doc").dataset.titulo;
      const a = el("a", { href: "#" + (findHeading(bloco) || ancora).id });
      a.appendChild(el("span", {}, titulo));
      a.appendChild(el("small", {}, "…" + texto.slice(Math.max(0, pos - 40), pos + 60).trim() + "…"));
      resultados.appendChild(a);
    });
  });
  if (!resultados.childElementCount) resultados.appendChild(el("small", {}, "Nada encontrado."));
});
function findHeading(bloco) {
  let n = bloco;
  while (n && !n.classList?.contains("doc")) {
    let irmao = n.previousElementSibling;
    while (irmao) { if (/^H[1-3]$/.test(irmao.tagName) && irmao.id) return irmao; irmao = irmao.previousElementSibling; }
    n = n.parentElement;
  }
  return null;
}

// Imagens ampliáveis
const zoom = document.getElementById("zoom");
document.querySelectorAll("main img").forEach(img => img.addEventListener("click", () => { zoom.querySelector("img").src = img.src; zoom.querySelector("img").alt = img.alt; zoom.style.display = "flex"; }));
zoom.addEventListener("click", () => zoom.style.display = "none");
document.addEventListener("keydown", e => { if (e.key === "Escape") zoom.style.display = "none"; });

// Tema e menu
const raiz = document.documentElement;
try { const salvo = localStorage.getItem("tema-portal"); if (salvo) raiz.dataset.tema = salvo; } catch (e) {}
document.getElementById("tema").addEventListener("click", () => {
  const escuro = raiz.dataset.tema ? raiz.dataset.tema === "escuro" : matchMedia("(prefers-color-scheme: dark)").matches;
  raiz.dataset.tema = escuro ? "claro" : "escuro";
  try { localStorage.setItem("tema-portal", raiz.dataset.tema); } catch (e) {}
});
document.getElementById("menu").addEventListener("click", () => document.querySelector("aside").classList.toggle("aberto"));

mostrar(location.hash);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Gera o portal de documentação em uma página HTML.")
    p.add_argument("--repo", default=".", help="raiz do repositório (padrão: pasta atual)")
    p.add_argument("--saida", default="docs/portal.html", help="arquivo gerado, relativo ao repositório")
    p.add_argument("--incluir", action="append", default=[], help="padrão glob extra de Markdown (pode repetir)")
    p.add_argument("--online", action="store_true", help="carrega o Mermaid do CDN em vez de embutir")
    a = p.parse_args()
    montar(a.repo, a.saida, a.incluir, a.online)
