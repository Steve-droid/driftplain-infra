# Monthly AWS cost budget — the financial backstop for the retained AWS services. Since the
# compute retirement (September 21, 2026) and HM8 (September 22, 2026) the account holds only
# persistent services: S3 (state, backups, ingestion sources), Secrets Manager, Route 53, Roles
# Anywhere and this budget — about $2–3/month gross. The limit is $10 (dev.tfvars): a threshold
# crossing means something unplanned is running, not that the home service is expensive. The
# P34b kill-switch chain (Lambda → CodeBuild teardown) was removed at HM8 with nothing left to
# tear down; alerts are email only. Lives in the PERSISTENT bootstrap stack.
#
# Budgets is a global service managed via the us-east-1 endpoint; the default (ap-south-1)
# provider handles that transparently, so no alias is needed on the budget itself — only on the
# SNS topic it notifies (see sns.tf). The budget picks up default_tags like any taggable
# resource (visible in state as tags_all).
#
# Each notification has TWO delivery channels:
#   - subscriber_email_addresses: budget-native email straight from AWS Budgets. This is the
#     RELIABLE channel — these mails carry no SNS "unsubscribe" link, so Gmail's link-prefetch
#     (security scanning) can't silently deactivate the subscription the way it does for SNS
#     email subscriptions. This is what actually guarantees the alert reaches the inbox.
#   - subscriber_sns_topic_arns: the SNS topic (see sns.tf), kept for the topic/subscription
#     pattern + future programmatic fan-out. Not relied on for email delivery.
#
# Credits: the account carries Free Tier credits (expire 2027-06-30). By default a cost budget
# nets credits out (include_credit = true), so the tracked "actual" spend would sit near $0 and
# NO threshold would fire until the credit was exhausted. cost_types below tracks GROSS usage
# instead, so the budget measures what the account actually burns regardless of who pays.

resource "aws_budgets_budget" "monthly_cost" {
  name         = "modelmatch-monthly-cost"
  budget_type  = "COST"
  limit_amount = var.budget_limit_amount
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Track gross usage: do not let applied credits (or refunds) lower the measured spend.
  cost_types {
    include_credit = false
    include_refund = false
  }

  # 80% of the cap, measured against ACTUAL spend — "you've already burned this much".
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }

  # 100% ACTUAL — the cap itself has been crossed (Budgets refreshes spend a few times a day, so
  # this fires up to ~12h late). With no compute left there is nothing to tear down automatically;
  # the email is the signal to look at Cost Explorer.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }

  # 100% FORECASTED — AWS projects the month's run-rate and warns before you actually hit the cap.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
  }
}
