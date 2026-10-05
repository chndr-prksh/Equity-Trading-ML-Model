# Use an official Miniconda3 base image
FROM --platform=linux/amd64 continuumio/miniconda3:4.10.3

# Set the working directory
WORKDIR /app

# Install necessary system packages
RUN apt-get update && apt-get install -y \
    build-essential \
    wget \
    default-libmysqlclient-dev 

# Install Python packages
COPY environment.yml .
RUN conda env create -f environment.yml

# Activate the environment
RUN echo "source activate my_env" > ~/.bashrc
ENV PATH /opt/conda/envs/my_env/bin:$PATH

COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the application code
COPY . /app

# Run the application
CMD ["bash", "-c", "source activate my_env && python main.py"]