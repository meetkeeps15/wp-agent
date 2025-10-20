$ws = New-Object System.Net.WebSockets.ClientWebSocket
$uri = [Uri] "ws://localhost/api/copilot/ws"
$cts = New-Object System.Threading.CancellationTokenSource
$ws.ConnectAsync($uri, $cts.Token).Wait()

# WebSocket endpoint expects {"message": "..."}
$msg = '{"message":"WS test"}'
$buffer = [System.Text.Encoding]::UTF8.GetBytes($msg)
$seg = New-Object System.ArraySegment[byte] -ArgumentList (,$buffer)
$ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).Wait()

$recvBuffer = New-Object byte[] 16384
$recvSegment = New-Object System.ArraySegment[byte] -ArgumentList (,$recvBuffer)
$rcv = $ws.ReceiveAsync($recvSegment, $cts.Token).Result

$text = [System.Text.Encoding]::UTF8.GetString($recvBuffer,0,$rcv.Count)
Write-Output "Received:"
Write-Output $text

$ws.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, "done", $cts.Token).Wait()