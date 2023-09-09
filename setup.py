from setuptools import setup, find_packages

setup(
    name="termqt",
    version="0.11",
    packages=find_packages(),

    author="Terry Geng",
    author_email="terry@terriex.com",
    description="A terminal emulator widget built on PyQt5.",
    keywords="terminal emulator pyqt",
    url="https://github.com/TerryGeng/termqt",
    install_requires=("PyQt5", "PyQt5-sip", "Qt.py", "pywinpty; platform_system=='Windows'"),
    classifiers=[
        "Environment :: X11 Applications :: Qt",
        "Operating System :: POSIX",
        "License :: OSI Approved :: GNU Lesser General Public License v2 "
        "or later (LGPLv2+)",
    ]
)
