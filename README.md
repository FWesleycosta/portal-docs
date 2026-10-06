# portal-docs

Gera e publica a documentação de um repositório como um portal em uma única página HTML: menu por categoria, busca, diagramas Mermaid e draw.io, tabelas filtráveis e pendências `[A CONFIRMAR]` reunidas.

A fonte da verdade continua sendo o Markdown do repositório (`README.md` e `docs/`). O portal é derivado e nunca é editado à mão.

## Como usar num repositório

1. Habilite o GitHub Pages com origem "GitHub Actions":
   `gh api -X POST repos/<dono>/<repo>/pages -f build_type=workflow`
2. Copie `exemplo/portal.yml` para `.github/workflows/portal.yml` e ajuste o `incluir`.
3. Fixe as actions por SHA: `scripts/fixar-actions.sh .github/workflows/portal.yml`.
4. A cada merge na main que mude a documentação, o portal é publicado no GitHub Pages.

> [!WARNING]
> Em repositórios privados de organizações fora do GitHub Enterprise Cloud, o site do GitHub Pages fica público. Não publique documentação interna dessa forma.

## Uso local

`python3 gerar-portal.py --repo <pasta> --saida docs/portal.html [--incluir 'modules/*/README.md'] [--online]`

Requer a biblioteca `markdown` (`pip install markdown`).

## Versionamento

SemVer com tags `vX.Y.Z`. Os repositórios consumidores fixam a action pelo SHA da tag, e o Dependabot propõe as atualizações.
