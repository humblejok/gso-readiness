# Windows PowerShell 5.1 / PowerShell 7 bridge. Fixed code; JSON input is data only.
# Uses the logged-on Windows identity, never exports a password or Git credential.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$client = $null
$response = $null
try {
    Add-Type -AssemblyName System.Net.Http
    $requestData = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $uri = [Uri]$requestData.url
    $collection = [Uri]$requestData.collection
    if ($uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Fragment -or
        $uri.Authority -ne $collection.Authority -or
        -not $uri.AbsolutePath.StartsWith($collection.AbsolutePath.TrimEnd('/') + '/', [StringComparison]::Ordinal) -or
        $requestData.method -notin @('GET', 'POST')) { throw 'Invalid destination' }
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseDefaultCredentials = $true
    $handler.UseCookies = $false
    # Python has already resolved saved proxy and NO_PROXY for this exact host.
    if ($requestData.proxy) {
        $proxyUri = [Uri]$requestData.proxy
        if ($proxyUri.Scheme -notin @('http', 'https') -or $proxyUri.UserInfo) { throw 'Invalid proxy' }
        $handler.Proxy = [System.Net.WebProxy]::new($proxyUri)
    } else {
        $handler.UseProxy = $false
    }
    # No certificate callback or TLS bypass: Windows certificate trust is required.
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(30)
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($requestData.method), $uri)
    $request.Headers.Accept.ParseAdd('application/json')
    if ($null -ne $requestData.body) {
        $request.Content = [System.Net.Http.StringContent]::new([string]$requestData.body, [System.Text.Encoding]::UTF8, 'application/json')
    }
    $response = $client.SendAsync($request, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    $status = [int]$response.StatusCode
    if ($status -notin @(200, 201)) {
        # Error pages can echo credentials/source; never read or return their bodies.
        [Console]::WriteLine((@{status=$status; body=''} | ConvertTo-Json -Compress))
        exit 0
    }
    $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $buffer = [byte[]]::new(8192)
    $memory = [System.IO.MemoryStream]::new()
    $cancellation = [System.Threading.CancellationTokenSource]::new(30000)
    try {
        while (($count = $stream.ReadAsync($buffer, 0, $buffer.Length, $cancellation.Token).GetAwaiter().GetResult()) -gt 0) {
            if ($memory.Length + $count -gt 10485760) { throw 'Response too large' }
            $memory.Write($buffer, 0, $count)
        }
        $body = [System.Text.UTF8Encoding]::new($false, $true).GetString($memory.ToArray())
        [Console]::WriteLine((@{status=$status; body=$body} | ConvertTo-Json -Compress))
    } finally {
        $cancellation.Dispose()
        $memory.Dispose()
        $stream.Dispose()
    }
} catch {
    [Console]::WriteLine('{"status":0,"body":""}')
    exit 1
} finally {
    if ($response) { $response.Dispose() }
    if ($client) { $client.Dispose() }
}
