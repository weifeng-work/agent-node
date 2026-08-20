# 清理 IKUN 侧陈旧 known_peers（保留真实 Admin-PC）
$nodes = (Invoke-RestMethod http://127.0.0.1:5177/api/nodes -TimeoutSec 5).nodes
$n = 0
foreach ($node in $nodes) {
  if ($node.nodeId -ne "node-PC-20231207KSGE-212c09") {
    $body = @{ node_id = $node.nodeId } | ConvertTo-Json -Compress
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5177/api/nodes/forget `
      -ContentType "application/json" -Body $body | Out-Null
    $n++
  }
}
Write-Output "IKUN: cleaned $n stale nodes"
$left = (Invoke-RestMethod http://127.0.0.1:5177/api/nodes -TimeoutSec 5).nodes
Write-Output ("left: " + (($left | ForEach-Object { $_.name }) -join ", "))
