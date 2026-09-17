# Explicit -var-file=dev.tfvars on every plan/test/apply. No deployment authorized.
aws_region                   = "ap-south-1"
home_server_account_id       = "957261948820"
home_server_identity_enabled = true
home_server_sessions_enabled = true
# Public CA verified against the September 13 enrollment record; never a private key.
# DER SHA-256: b125437b857bf35561dd93e589b477d7284fce7e457a953ff83db859839b00b4
home_server_ca_certificate_pem = <<-PEM
-----BEGIN CERTIFICATE-----
MIIESjCCArKgAwIBAgIUNSOVaUhLeP+kEwY6SocztMk54c8wDQYJKoZIhvcNAQEL
BQAwKzEpMCcGA1UEAwwgZHJpZnRwbGFpbi1ob21lLXNlcnZlci1pc3N1ZXItdjEw
HhcNMjYwOTEzMDEzMzE5WhcNMjgwOTEyMDEzODE5WjArMSkwJwYDVQQDDCBkcmlm
dHBsYWluLWhvbWUtc2VydmVyLWlzc3Vlci12MTCCAaIwDQYJKoZIhvcNAQEBBQAD
ggGPADCCAYoCggGBAKTispahkH6e7ysqh5TLSWih2je9Aov54dQFyrUGJ3ptfuIo
Vw6amW04065TLuGfgeVRfCHmk8QGfJejmwL2El3gZIzAMOSBM11o9xALimcfrJFb
2HirhALG7Nc/20EowSXtE5AhArWqZzoNu764A0Jg2HyAxHIQQhRlv6zRbkiiPZ0l
oWK4s+4jbCfjdN8Idd60aXar0G4yutQ8E3AJt0mzUOK5DfIx1pJPrQ8y0aco6I7X
eT3YnC0JaGGXzsg0dtQ2yqq+V9OQuo0nDHci0wms/taBrkXtDD+Ov3cd69skQJYu
yTZnUvwYS+5OU28BCReJhqgBwR9hmbTm5zwIr6q7sXdSHe3ADNOUtCbFRM95e12K
mmZAvSScoBxT2SL9Gq+hvz40EK1yZ51TWZwcvfW/Eqcw+tCD/zOZIGBMISzO9RE4
am6rQOVkoC2qeLMmMPtx2mnkgDs0d4VZZicT/iOhJRALOpGBQQUHjE05cLX6nou8
0mc0zs/4qmgerpVFnQIDAQABo2YwZDASBgNVHRMBAf8ECDAGAQH/AgEAMA4GA1Ud
DwEB/wQEAwIBBjAdBgNVHQ4EFgQUiMEMup7uIa/zU7F2kI2OYzIIsScwHwYDVR0j
BBgwFoAUiMEMup7uIa/zU7F2kI2OYzIIsScwDQYJKoZIhvcNAQELBQADggGBACKt
9QCGMgYC8Xk69V10sY4XdJ7gh3jtqD/fYI7Z7yXIjWYkLRrawnjzWz4BJioJ6tFd
YjNVe76JyS2M8waw+kx+DevUl6HmdzXOCwKLRacMmOiFwMibwasdTKBFV9aB//AT
bInQcHABXaJrBmPqMX80eKb0WsMeqPf9NSAZW7CyHUWb5APVE3/Hr12xG2lqLcF8
SdtkAvWF6HKu302wECwP84YdlTZW6KvZiEMci9kAUvPWOfXuGPsvWz3PYXRxDepf
xO3Tsnx3xNKbWY6yGkB5wuOS8Z5hrt9DVvCi7NX8jrKEKCfUqCtJ9bFFoVO+kOWi
Pj5HPiDl9utFPffWVBKd+1tWCLy+Fd5aCURB5eHogVrpYjSXugmOxFDhOh2o9WOi
G8A9PInFn9XRUP8LZXzV9qGvCk3LMSRObN6FkpAefJe8993gMUsQiP1+MfKn1115
HIfMji7S+oXdjaj7QxR4tUznWkCR7PulbWmzJyLbG42qXiFBPfb1C19qDN9yLQ==
-----END CERTIFICATE-----
PEM
home_server_backup_bucket_name = "modelmatch-home-server-backups-957261948820"
# E21/HM4: the existing bootstrap ingestion bucket (durable S3 blob store); object-level only.
home_server_ingestion_bucket_name = "modelmatch-ingestion-sources-957261948820"
home_server_bedrock_profile_ids   = ["apac.amazon.nova-lite-v1:0", "global.amazon.nova-2-lite-v1:0"]
home_server_bedrock_model_ids     = ["amazon.nova-lite-v1:0", "amazon.nova-2-lite-v1:0"]
home_server_teardown_role_name    = "modelmatch-platform-teardown-codebuild"
