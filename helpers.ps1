function New-DocJob($name) {
  $j = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/jobs" -ContentType "application/json" -Body (@{filename=$name} | ConvertTo-Json)
  Set-Content $name "hello"
  curl.exe -s -o NUL -X PUT --upload-file $name "$($j.upload_url)"
  Invoke-RestMethod -Method Post "http://127.0.0.1:8000/jobs/$($j.job_id)/start" | Out-Null
  $j.job_id
}

function Watch-DocJob($id) {
  if (-not $id -or $id -is [array]) { Write-Host "Need a single job id, got: '$id'"; return }
  for ($i = 0; $i -lt 60; $i++) {
    $s = Invoke-RestMethod "http://127.0.0.1:8000/jobs/$id"
    "{0,-10} {1,-9} {2,3}%  attempts={3}  {4}" -f $s.status, $s.stage, $s.progress, $s.attempts, $s.error
    if ($s.status -in 'COMPLETED','FAILED') { break }
    Start-Sleep 2
  }
}

function Stop-DocWorker {
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*celery*worker*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function New-HookJob($name, $url) {
  $j = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/jobs" -ContentType "application/json" -Body (@{filename=$name; webhook_url=$url} | ConvertTo-Json)
  Set-Content $name "hello"
  curl.exe -s -o NUL -X PUT --upload-file $name "$($j.upload_url)"
  Invoke-RestMethod -Method Post "http://127.0.0.1:8000/jobs/$($j.job_id)/start" | Out-Null
  $j.job_id
}

function New-FileJob($path) {
  $name = Split-Path $path -Leaf
  $j = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/jobs" -ContentType "application/json" -Body (@{filename=$name} | ConvertTo-Json)
  curl.exe -s -o NUL -X PUT --upload-file $path "$($j.upload_url)"
  Invoke-RestMethod -Method Post "http://127.0.0.1:8000/jobs/$($j.job_id)/start" | Out-Null
  $j.job_id
}

function Show-DocJob($id) {
  for ($i = 0; $i -lt 30; $i++) {
    $s = Invoke-RestMethod "http://127.0.0.1:8000/jobs/$id"
    if ($s.status -in 'COMPLETED','FAILED') { break }
    Start-Sleep 1
  }
  $s | ConvertTo-Json -Depth 6
}
