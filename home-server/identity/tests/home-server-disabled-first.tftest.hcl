mock_provider "aws" {}

# Consume the explicit dev.tfvars without overrides: this is the proposed rollout.
run "enrolled_ca_resources_on_sessions_off" {
  command = plan
  assert {
    condition = (
      var.home_server_identity_enabled && !var.home_server_sessions_enabled &&
      length(aws_rolesanywhere_trust_anchor.home_server) == 1 &&
      length(aws_iam_role.home_server) == 2 &&
      length(aws_iam_role_policy.home_server) == 2 &&
      length(aws_rolesanywhere_profile.home_server) == 2 &&
      length(aws_iam_role_policy.home_server_teardown_protection) == 1
    )
    error_message = "The proposed inputs must create exactly eight resources with sessions off."
  }
  assert {
    condition = (
      trimspace(var.home_server_ca_certificate_pem) == trimspace(file("${path.module}/../home-server-issuer.crt")) &&
      alltrue([for anchor in aws_rolesanywhere_trust_anchor.home_server : !anchor.enabled]) &&
      alltrue([for profile in aws_rolesanywhere_profile.home_server : !profile.enabled])
    )
    error_message = "The plan must bind the enrolled public CA and leave every anchor/profile disabled."
  }
}
