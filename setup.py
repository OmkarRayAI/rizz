from setuptools import setup, find_packages
import os

def read_requirements():
    here = os.path.abspath(os.path.dirname(__file__))
    
    # Try multiple possible locations for requirements.txt
    possible_paths = [
        os.path.join(here, 'requirements.txt'),
        os.path.join(here, 'rizz', 'requirements.txt'),
    ]
    
    for req_path in possible_paths:
        if os.path.exists(req_path):
            with open(req_path, 'r') as f:
                return [line.strip() for line in f if line.strip() and not line.startswith('#')]
    
    return []

setup(
    name="rizz",
    version="0.1.0",
    packages=find_packages(),
    install_requires=read_requirements(),
    entry_points={
        "console_scripts": [
            "rizz = rizz.cli:main",
        ],
    },
)