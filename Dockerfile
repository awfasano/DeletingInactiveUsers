# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# --no-cache-dir: Disables the cache which is not needed for production images
# --require-hashes: Ensures dependencies are the ones you expect
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container
COPY . .

# The CMD instruction specifies the command to run when the container starts.
# We use the Functions Framework to serve the function.
# The --target flag specifies the name of the function in main.py to execute.
CMD ["functions-framework", "--target=cleanup_firestore"]
