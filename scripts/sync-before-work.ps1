[CmdletBinding()]
param(
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repo

if (-not (Test-Path ".git")) {
    throw "Este directorio aun no es un repositorio Git. Ejecute primero la configuracion inicial del README."
}

if (git status --porcelain) {
    throw "Hay cambios locales. Confirme o guarde esos cambios antes de sincronizar."
}

git fetch --prune origin
if ($LASTEXITCODE -ne 0) { throw "No fue posible consultar GitHub." }

git switch $Branch
if ($LASTEXITCODE -ne 0) { throw "No fue posible cambiar a la rama $Branch." }

git pull --ff-only origin $Branch
if ($LASTEXITCODE -ne 0) { throw "La rama local divergio de GitHub; se requiere revision manual." }

Write-Host "Sincronizado con origin/$Branch en $(git rev-parse --short HEAD). Ya puede comenzar a trabajar."

