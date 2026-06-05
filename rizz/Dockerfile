# Use an official Python runtime as a base image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies that might be needed for Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first for caching dependencies
COPY requirements.txt .

# Install dependencies in a single layer
RUN pip install --no-cache-dir -r requirements.txt

# Copy only necessary files (exclude .git, __pycache__, etc.)
COPY *.py .
COPY tools/ tools/

# Command to run the example.py script and listen for changes
CMD ["watchmedo", "auto-restart", "--patterns=*.py", "--recursive", "python", "aci_example.py"]