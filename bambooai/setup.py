from setuptools import setup, find_packages
import os

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name='bambooai',
    version='0.3.52',
    description='A lightweight library for working with pandas dataframes using natural language queries',
    long_description=long_description,
    long_description_content_type="text/markdown",
    author='Palo Galko',
    packages=find_packages(),
    install_requires=[
        'openai',
        'tiktoken',
        'pandas',
        'termcolor',
        'newspaper3k',
        'selenium',
        'pinecone',
        'sentence-transformers',
        'google-generativeai',
        'groq',
        'ollama',
        'anthropic',
        'mistralai'
    ],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
    ],
)