import os
import datetime
from google.cloud import firestore
import firebase_admin
from firebase_admin import firestore

# Initialize Firebase Admin SDK
# The SDK will automatically use the default service account credentials
# in the Cloud Functions environment.
firebase_admin.initialize_app()


def cleanup_firestore(event, context):
    """
    Cloud Function entry point. Triggered by Cloud Scheduler.
    Cleans up old activeUsers, updates the count, and deletes old messages.
    """
    print("Starting Firestore cleanup job...")
    db = firestore.client()

    # Get the current time (UTC for consistent comparison)
    now = datetime.datetime.now(datetime.timezone.utc)

    # --- 1. Clean up activeUsers and update count ---
    # Set the time threshold: 10 minutes ago
    active_user_threshold = now - datetime.timedelta(minutes=10)

    print(f"Cleaning activeUsers with 'lastUpdate' before {active_user_threshold.isoformat()}")

    try:
        spaces_ref = db.collection('Spaces')
        for space in spaces_ref.stream():
            space_id = space.id
            space_ref = space.reference  # Get a reference to the main space document
            print(f"Processing space: {space_id}")

            active_users_ref = space.reference.collection('activeUsers')

            # Query for users whose last update was more than 10 minutes ago
            old_users_query = active_users_ref.where('lastUpdate', '<', active_user_threshold)

            # Use a batch to delete multiple documents efficiently
            batch = db.batch()
            docs_to_delete_count = 0
            for doc in old_users_query.stream():
                print(f"  - Scheduling deletion for activeUser: {doc.id} in space: {space_id}")
                batch.delete(doc.reference)
                docs_to_delete_count += 1

            if docs_to_delete_count > 0:
                batch.commit()
                print(f"  - Successfully deleted {docs_to_delete_count} old activeUser(s) from space: {space_id}")
            else:
                print(f"  - No old activeUsers to delete in space: {space_id}")

            # --- NEW: Count remaining users and update the space document ---
            # Stream the documents again to get an accurate live count.
            remaining_users_stream = active_users_ref.stream()
            # This is an efficient way to count all items in an iterator
            remaining_user_count = sum(1 for _ in remaining_users_stream)

            print(f"  - Found {remaining_user_count} remaining active user(s).")

            # Update the currentUserCount field on the parent space document
            space_ref.update({'currentUserCount': remaining_user_count})
            print(f"  - Updated 'currentUserCount' to {remaining_user_count} for space: {space_id}")

    except Exception as e:
        print(f"Error during activeUsers cleanup and count update: {e}")

    # --- 2. Clean up old messages ---
    # Set the time threshold: 24 hours ago
    message_threshold = now - datetime.timedelta(hours=24)

    print(f"\nCleaning messages with 'timestamp' before {message_threshold.isoformat()}")

    try:
        spaces_ref = db.collection('Spaces')
        for space in spaces_ref.stream():
            space_id = space.id
            print(f"Processing messages for space: {space_id}")

            messages_ref = space.reference.collection('messages')

            # Query for messages older than 24 hours
            old_messages_query = messages_ref.where('timestamp', '<', message_threshold)

            # Use a batch for efficient deletion
            batch = db.batch()
            msgs_to_delete_count = 0
            for msg in old_messages_query.stream():
                print(f"  - Scheduling deletion for message: {msg.id} in space: {space_id}")
                batch.delete(msg.reference)
                msgs_to_delete_count += 1

            if msgs_to_delete_count > 0:
                batch.commit()
                print(f"  - Successfully deleted {msgs_to_delete_count} old message(s) from space: {space_id}")
            else:
                print(f"  - No old messages to delete in space: {space_id}")

    except Exception as e:
        print(f"Error during messages cleanup: {e}")

    print("\nFirestore cleanup job finished.")
    return 'OK', 200
