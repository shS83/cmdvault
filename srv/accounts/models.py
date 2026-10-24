from django.db import models


class LoginBucket(models.Model):
    # HMAC of account/IP scope; no credentials or raw usernames/IPs stored.
    key = models.CharField(max_length=64, primary_key=True)
    window = models.BigIntegerField()
    attempts = models.PositiveIntegerField(default=0)
