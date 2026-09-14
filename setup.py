import io

from setuptools import find_packages, setup

with io.open("README.md", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="diffimpactscout",
    version="0.3.0",
    description="Incremental pre-push guard and AST blast-radius impact analyzer for git repositories.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    license="MIT",
    author="DiffImpactScout contributors",
    python_requires=">=3.8",
    packages=find_packages("src"),
    package_dir={"": "src"},
    package_data={"diffimpactscout": ["profiles/*.json"]},
    entry_points={"console_scripts": ["diffimpactscout=diffimpactscout.cli:main"]},
    install_requires=[],
)