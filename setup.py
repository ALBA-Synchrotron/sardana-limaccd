from setuptools import setup, find_packages

# The version is updated automatically with bumpversion
# Do not update manually
__version = "0.6.1"


setup(
    name="sardana_limaccd",
    version=__version,
    author="ALBA controls team",
    author_email="controls@cells.es",
    description="LimaCCD Sardana plugin (controllers, macros, tools)",
    license="GPLv3",
    url="https://github.com/ALBA-Synchrotron/sardana-limaccd",
    packages=find_packages(),
    install_requires=["sardana"],
    python_requires=">=3.5"
)
