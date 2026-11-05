kind delete cluster --name pr3
if ($LASTEXITCODE -ne 0) { throw 'kind delete failed' }
