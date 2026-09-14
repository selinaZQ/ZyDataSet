$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent (Split-Path -Parent $ProjectDir)
$BundledPython = "C:\Users\Belinda\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$OcrPython = Join-Path $RepoDir ".venv-ocr\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $BundledPython)) {
    throw "Bundled Python not found: $BundledPython"
}
if (-not (Test-Path -LiteralPath $OcrPython)) {
    throw "OCR environment not found: $OcrPython"
}

& $BundledPython -X utf8 (Join-Path $ProjectDir "scripts\extract_native.py")
& $OcrPython -X utf8 (Join-Path $ProjectDir "scripts\extract_ocr.py")
& $BundledPython -X utf8 (Join-Path $ProjectDir "scripts\assemble.py")
& $BundledPython -X utf8 (Join-Path $ProjectDir "scripts\structure.py")
& $BundledPython -X utf8 (Join-Path $ProjectDir "scripts\qa.py")
