# Comments

The **Comments** system provides internal team notes within cases and subjects. Comments are only visible to logged-in users.

## Posting a Comment

Comments can be added from:

- **Case detail page** — general case comments
- **Subject detail page** — subject-specific comments
- **Finding detail** — finding-specific notes

Click **Add Comment** or type in the text field and click **Save**.

## Comment Types

| Type | Scope |
|------|-------|
| **General** | General note |
| **Note** (subject) | Subject note (migrated from the old `notes` field) |
| **Internal** | Internal team comment |

## Editing a Comment

Comments can be edited:

1. Click the ✏️ icon next to the comment
2. Modify the text
3. Click **Save**

### Edit History

Every edit is saved in the **Comment Edit History**. You can view the original and previous versions via the 📋 button on the comment.

## Deleting a Comment

Only the author or an admin can delete a comment. Click the 🗑️ icon to delete.

## Notes vs Comments

The old `notes` field on Subjects has been migrated to the Comments model. Only subjects without existing comments with the same content get a comment created during migration (idempotent).
