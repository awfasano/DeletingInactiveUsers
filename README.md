Firestore Cleanup Cloud Function
This project contains a Google Cloud Function designed to perform periodic maintenance on a Firestore database. It is configured for continuous deployment using Cloud Build, Docker, and a Git-based workflow.

The function runs on an hourly schedule and performs the following cleanup tasks:

Inactive User Cleanup: In each Spaces document, it checks the activeUsers subcollection and deletes any user document where the lastUpdate timestamp is more than 10 minutes old.

User Count Update: After cleaning up inactive users, it recounts the remaining users in the activeUsers subcollection and updates the currentUserCount field on the parent Spaces document.

Old Message Cleanup: In each Spaces document, it checks the messages subcollection and deletes any message document where the timestamp is more than 24 hours old.

Project Structure
The repository contains the following files:

main.py: The Python source code for the Cloud Function. It contains all the logic for connecting to Firestore and performing the cleanup operations.

requirements.txt: A list of Python dependencies  required to run the function (firebase-admin and functions-framework).

Dockerfile: Defines the container environment for the function.  It sets up a Python environment, installs dependencies, and copies the source code.

cloudbuild.yaml: The configuration file for Cloud Build. It defines a CI/CD pipeline that builds the Docker image, pushes it to Artifact Registry, and deploys it as a 2nd Gen Cloud Function with a schedule.

Deployment Guide
Follow these steps to deploy the function to your Google Cloud project.

Prerequisites
A Google Cloud Project.

A Git repository (GitHub, Cloud Source Repositories, etc.) containing the files from this project.

The gcloud command-line tool installed and authenticated.

Step 1: Enable APIs
Enable the necessary APIs in your project.

gcloud services enable cloudbuild.googleapis.com
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable firestore.googleapis.com

Step 2: Create Artifact Registry Repository
Create a repository to store the Docker images. The name and location should match the substitutions in cloudbuild.yaml.

# Using the values from cloudbuild.yaml
gcloud artifacts repositories create cleanup-functions-repo \
    --repository-format=docker \
    --location=us-east5 \
    --description="Repository for cleanup function images"

Step 3: Grant IAM Permissions
The Cloud Build service account needs permission to deploy the function and manage its identity.

Navigate to the IAM page in the Google Cloud Console.

Find the service account ending in @cloudbuild.gserviceaccount.com.

Grant it the following roles:

Cloud Functions Admin

Service Account User

Step 4: Create the Cloud Build Trigger
Set up a trigger to automatically build and deploy when you push to

