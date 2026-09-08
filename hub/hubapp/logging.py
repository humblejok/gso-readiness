"""Avoid logging verification/invitation credentials embedded in URL paths."""

import logging
import re


class CredentialPathFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = re.sub(r"(/(?:accounts/verify|invitations)/)[^/\s]+", r"\1[redacted]", message)
        message = re.sub(r"(/accounts/reset/)[^\s]+", r"\1[redacted]", message)
        record.msg, record.args = message, ()
        return True
