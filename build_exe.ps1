$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Environnement virtuel introuvable. Cree d'abord .venv."
}
$pythonExe = ".\.venv\Scripts\python.exe"

if (Test-Path "dist\PyRATracker.exe") {
    Remove-Item "dist\PyRATracker.exe" -Force
}
if (Test-Path "dist\PyRA.exe") {
    Remove-Item "dist\PyRA.exe" -Force
}

$iconPath = $null

# Priorite absolue aux icones .ico explicites.
$preferredIcons = @("icon.ico", "app.ico", "PyRA.ico")
foreach ($iconName in $preferredIcons) {
    if (Test-Path $iconName) {
        $iconPath = (Resolve-Path $iconName).Path
        break
    }
}

# Sinon, priorite explicite a l'image demandee.
if (-not $iconPath -and (Test-Path "PyRA_icon_256_centered.png")) {
    $iconPath = (Resolve-Path "PyRA_icon_256_centered.png").Path
}

# Priorite aux images utilisateur a la racine (on ignore les icones generees).
if (-not $iconPath) {
    $candidateImage = Get-ChildItem -File -Path . |
        Where-Object {
            $_.Extension -in @(".png", ".jpg", ".jpeg") -and
            $_.Name -notin @("PyRA.generated.ico", "PyRA.ico")
        } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($candidateImage) {
        $iconPath = $candidateImage.FullName
    }
}

$pyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "PyRA"
)
if ($iconPath) {
    Write-Host "Icone source detectee:" $iconPath
    $iconExt = [System.IO.Path]::GetExtension($iconPath).ToLowerInvariant()
    if ($iconExt -ne ".ico") {
        Write-Host "Preparation d'une icone .ico (format Windows standard) ..."
        & $pythonExe -m pip install --disable-pip-version-check --quiet pillow
        if ($LASTEXITCODE -ne 0) {
            throw "Impossible d'installer Pillow. Convertis l'image en .ico ou verifie l'acces reseau."
        }

        $generatedIconPath = (Resolve-Path ".").Path + "\PyRA.generated.ico"
        & $pythonExe -c "from PIL import Image, ImageChops; import sys; src, dst = sys.argv[1], sys.argv[2]; img = Image.open(src).convert('RGBA'); alpha_box = img.split()[-1].getbbox(); img = img.crop(alpha_box) if alpha_box else img; bg = Image.new('RGBA', img.size, img.getpixel((0, 0))); diff = ImageChops.difference(img, bg); bg_box = diff.getbbox(); img = img.crop(bg_box) if bg_box else img; side = max(img.width, img.height); square = Image.new('RGBA', (side, side), (0, 0, 0, 0)); square.paste(img, ((side - img.width)//2, (side - img.height)//2), img); target = 512; resampling = getattr(Image, 'Resampling', Image).LANCZOS; out = square.resize((target, target), resampling); out.save(dst, format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])" $iconPath $generatedIconPath
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $generatedIconPath)) {
            throw "Impossible de preparer une icone .ico."
        }
        $iconPath = $generatedIconPath
    }

    Write-Host "Icone finale:" $iconPath
    $pyInstallerArgs += @("--icon", $iconPath)
    $pyInstallerArgs += @("--add-data", "$iconPath;.")
} else {
    Write-Host "Aucune icone detectee a la racine. Build sans icone personnalisee."
}
$pyInstallerArgs += "app.py"

& $pythonExe -m PyInstaller @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Echec de PyInstaller."
}

# Rafraichit le cache d'icones de l'explorateur Windows.
if (Get-Command ie4uinit.exe -ErrorAction SilentlyContinue) {
    Start-Process -FilePath "ie4uinit.exe" -ArgumentList "-ClearIconCache" -WindowStyle Hidden -Wait -ErrorAction SilentlyContinue | Out-Null
    Start-Process -FilePath "ie4uinit.exe" -ArgumentList "-show" -WindowStyle Hidden -Wait -ErrorAction SilentlyContinue | Out-Null
}

Write-Host "Build termine: dist\PyRA.exe"
