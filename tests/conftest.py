import pytest

from helpers import GitRepo, SCENARIOS


@pytest.fixture(params=SCENARIOS)
def gitrepo(request, tmp_path):
    repo = GitRepo.scenario(request.param, str(tmp_path))
    repo.scenario = request.param
    yield repo
    repo.remove()


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: fast git-only fixture smoke tests")