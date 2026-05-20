#!/usr/bin/env python3
"""
Migration script: Backfill existing tags from problems into the tags collection.

This script scans all problems, extracts unique tag names per user,
and creates tag documents for any tags that don't already exist.

Idempotent: Running multiple times has no effect since it only creates
tags that don't already exist.

Usage:
    python scripts/migrate_tags.py [--dry-run]

Options:
    --dry-run    Show what would be migrated without making changes
"""

import argparse
import asyncio
from datetime import UTC, datetime

from bson import ObjectId
from pymongo import AsyncMongoClient

from app.infrastructure.config.settings import get_settings


async def migrate_tags(dry_run: bool = False) -> None:
    """Migrate existing tags from problems to the tags collection."""
    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_uri)
    db = client[settings.mongodb_database]

    print(f"Connecting to MongoDB: {settings.mongodb_database}")
    print(f"Dry run: {dry_run}")

    # Get all unique tag names per user from non-deleted problems
    pipeline = [
        {"$match": {"isDeleted": False, "tags": {"$ne": [], "$exists": True}}},
        {"$unwind": "$tags"},
        {"$group": {"_id": {"userId": "$userId", "tag": "$tags"}}},
        {"$group": {
            "_id": "$_id.userId",
            "tags": {"$push": "$_id.tag"}
        }},
    ]

    cursor = db["problems"].aggregate(pipeline)
    user_tag_groups = await cursor.to_list(length=None)

    total_users = len(user_tag_groups)
    total_tags_to_migrate = sum(len(g["tags"]) for g in user_tag_groups)
    print(f"Found {total_users} users with {total_tags_to_migrate} unique tags in problems")

    migrated_count = 0
    skipped_count = 0

    for group in user_tag_groups:
        user_id = group["_id"]
        tags = group["tags"]

        # Check which tags already exist for this user
        existing_tags = await db["tags"].find(
            {"userId": user_id, "name": {"$in": tags}}
        ).to_list(length=None)
        existing_names = {tag["name"] for tag in existing_tags}

        # Create documents for new tags
        now = datetime.now(UTC)
        new_tags = [
            {
                "_id": ObjectId(),
                "userId": user_id,
                "name": name,
                "createdAt": now,
                "updatedAt": now,
            }
            for name in tags
            if name not in existing_names
        ]

        if new_tags:
            if dry_run:
                print(f"  User {user_id}: Would create {len(new_tags)} tags: {new_tags}")
                migrated_count += len(new_tags)
            else:
                try:
                    await db["tags"].insert_many(new_tags, ordered=False)
                    print(f"  User {user_id}: Created {len(new_tags)} tags")
                    migrated_count += len(new_tags)
                except Exception as e:
                    # BulkWriteError from duplicates is expected with ordered=False
                    # but we filter duplicates above, so this shouldn't happen
                    print(f"  User {user_id}: Error creating tags: {e}")

        skipped_count += len(existing_names)

    print(f"\nMigration summary:")
    print(f"  Tags migrated: {migrated_count}")
    print(f"  Tags already existing (skipped): {skipped_count}")

    if dry_run:
        print("This was a dry run. No changes were made.")

    await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate existing tags from problems to tags collection")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be migrated without making changes")
    args = parser.parse_args()

    asyncio.run(migrate_tags(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
