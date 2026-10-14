class ImportFailed(Exception):
    pass


class QuotaExceeded(ImportFailed):
    pass


class SiteUnavailable(ImportFailed):
    pass
