$file   = "C:\Users\hduenas\OneDrive - Go Daddy.com, LLC, A Delaware Company\Desktop\Claude\claudioui_server.ps1"
$tmpCss = "C:\Users\hduenas\OneDrive - Go Daddy.com, LLC, A Delaware Company\Desktop\Claude\new_style_temp.txt"

$content = [System.IO.File]::ReadAllText($file,   [System.Text.Encoding]::UTF8)
$newCSS  = [System.IO.File]::ReadAllText($tmpCss, [System.Text.Encoding]::UTF8)

$s1 = $content.IndexOf('<style>')
$s2 = $content.IndexOf('</style>') + 8

if ($s1 -lt 0 -or $s2 -lt 8) { Write-Host "ERROR: style block not found"; exit 1 }

$preconnect = '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
$newContent = $content.Substring(0, $s1) + $preconnect + "`r`n  " + $newCSS + $content.Substring($s2)

[System.IO.File]::WriteAllText($file, $newContent, [System.Text.Encoding]::UTF8)
Write-Host "Done - old $($s2 - $s1) chars, new $($newCSS.Length) chars"
