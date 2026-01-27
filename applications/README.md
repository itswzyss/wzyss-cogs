# Applications

A Red-DiscordBot cog that implements a server application system where new members must apply before gaining full access to the server. Similar to Discord's built-in Server Applications feature.

## Installation

To install this cog, run the following commands in your Red bot:

```
[p]repo add wzyss-cogs https://github.com/yourusername/wzyss-cogs
[p]cog install wzyss-cogs applications
[p]load applications
```

## Setup

### 1. Configure the Restricted Role

Before enabling the system, you need to set up a restricted role in Discord:

1. Create a new role (e.g., "Application Pending")
2. In Server Settings → Roles, configure this role to:
   - **Deny** "View Channels" permission for all categories/channels
   - **Allow** "View Channels" permission for the application category only
3. Set this role as the restricted role in the cog

### 2. Basic Configuration

#### Enable the system

```
[p]applications toggle True
```

#### Set the restricted role

```
[p]applications restrictedrole @Application Pending
```

#### Set the category for application channels

```
[p]applications category @Applications
```

#### Set the log channel

```
[p]applications logchannel #application-logs
```

This channel will receive logs of all application submissions, approvals, and denials.

#### Set the notification role

```
[p]applications notificationrole @Application Reviewers
```

This role will be pinged when new applications are submitted.

#### Set cleanup delay

```
[p]applications cleanupdelay 24
```

Set the delay in hours before application channels are automatically deleted after approval or denial. Default is 24 hours.

### 3. Configure Bypass Roles (Optional)

Members with bypass roles will skip the application process entirely:

```
[p]applications bypassrole add @Patreon
[p]applications bypassrole add @Staff
```

To remove a bypass role:

```
[p]applications bypassrole remove @Patreon
```

### 4. Customize the Application Form

The cog comes with default form fields, but you can customize them:

#### Interactive Field Manager (Recommended)

Use the interactive UI to manage fields with buttons:

```
[p]applications field manager
```

or

```
[p]applications field ui
```

This opens an interactive interface where you can:
- ➕ **Add Field** - Create a new field with a modal form
- ✏️ **Edit Field** - Select and edit an existing field
- 🗑️ **Delete Field** - Remove a field (with confirmation)
- ⬆️ **Move Up** - Reorder fields (move selected field up)
- ⬇️ **Move Down** - Reorder fields (move selected field down)
- 🔄 **Refresh** - Update the field list display

#### List current fields

```
[p]applications field list
```

#### Add a new field

```
[p]applications field add <name> <label> <type> [required] [options_or_confirm_text]

Types: text (short), paragraph (long), number, select (multiple choice), confirm (text confirmation)
```

For select fields, provide options separated by commas.
For confirm fields, provide the exact text users must type.

Examples:

```
[p]applications field add age "What is your age?" text True
[p]applications field add experience "Tell us about your experience" paragraph True
[p]applications field add discord "How long have you used Discord?" number False
[p]applications field add agreement "Do you agree to the rules?" select True "Yes,No"
[p]applications field add experience_level "Experience Level" select True "Beginner,Intermediate,Advanced,Expert"
[p]applications field add rules_confirm "I agree to the server rules" confirm True "I agree"
```

#### Set options for a select field

```
[p]applications field options <name> <options>

Options should be separated by commas.
```

Example:

```
[p]applications field options agreement "Yes,No,Maybe"
```

#### Set confirmation text for a confirm field

```
[p]applications field confirmtext <name> <text>
```

Example:

```
[p]applications field confirmtext rules_confirm "I agree"
```

#### Remove a field

```
[p]applications field remove <name>
```

## Usage

### How It Works

1. **User Joins**: When a new member joins the server:
   - If they have a bypass role → They gain full access immediately
   - If they don't → They receive the restricted role and a private application channel is created

2. **Application Process**:
   - User sees a welcome message in their private channel
   - User clicks "Start Application" button
   - User fills out the form (Discord modal)
   - Application is submitted and stored
   - **Application is logged to the log channel (if configured)**
   - **Notification role is pinged (if configured)**

3. **Review Process**:
   - Admins/managers can view, approve, or deny applications
   - On approval: Restricted role is removed → User gains full access → **Logged to log channel** → **Channel cleanup scheduled**
   - On denial: User is notified with reason → **Logged to log channel with reason** → **Channel cleanup scheduled**

4. **Cleanup Process**:
   - After approval or denial, channels are scheduled for deletion
   - Channels are automatically deleted after the configured delay (default: 24 hours)
   - Cleanup runs automatically every hour
   - Manual cleanup can be triggered with `[p]applications cleanup`

### Admin Commands

#### View Settings

```
[p]applications settings
```

#### Manual cleanup

Manually trigger cleanup of expired application channels:

```
[p]applications cleanup
```

This will immediately delete any channels that have passed their cleanup time.

#### View Applications

List all pending applications:

```
[p]applications list
```

View a specific application:

```
[p]applications view @user
```

#### Approve/Deny Applications

Approve an application:

```
[p]applications approve @user
```

Deny an application (with optional reason):

```
[p]applications deny @user This server is not a good fit for you.
```

#### Close Application Channels

Close/delete an application channel:

```
[p]applications close @user
```

## Permissions Required

The bot needs the following permissions:

- **Manage Roles** - To assign/remove the restricted role
- **Manage Channels** - To create and delete application channels
- **Send Messages** - To send welcome messages and notifications
- **Embed Links** - To display application information
- **Read Message History** - To view channel history

## Logging and Cleanup

### Application Logging

When a log channel is configured, all application events are logged:
- **Submissions**: User info, form responses, timestamp
- **Approvals**: Who approved, when, user info
- **Denials**: Who denied, when, reason, user info

The notification role (if configured) will be pinged in the log channel when new applications are submitted.

### Channel Cleanup

Application channels are automatically deleted after approval or denial:
- Default delay: 24 hours (configurable)
- Cleanup runs automatically every hour
- Channels scheduled for cleanup are tracked until deletion
- Manual cleanup can be triggered at any time

## Important Notes

### Restricted Role Setup

The restricted role **must** be configured manually in Discord to:
- Deny "View Channels" for all categories/channels
- Allow "View Channels" for the application category only

This is a Discord server-side configuration that cannot be automated by the bot.

### Channel Permissions

Application channels are created with the following permissions:
- **@everyone**: Deny all
- **Restricted Role**: Allow view/send
- **Applicant**: Allow view/send
- **Admin Roles**: Allow all (manage permissions)

### Data Storage

The cog stores:
- Application responses and status
- Channel associations
- Submission timestamps
- Approval/denial information (including denial reasons)
- Cleanup scheduling timestamps

Application data is retained until:
- The user leaves the server (automatic cleanup)
- An admin manually closes the application channel
- The channel is automatically deleted after cleanup delay
- The application is deleted via commands

## Troubleshooting

### Users aren't getting application channels

1. Check if the system is enabled: `[p]applications settings`
2. Verify the category is set correctly
3. Ensure the bot has "Manage Channels" permission
4. Check bot logs for errors

### Restricted role isn't working

1. Verify the role is set: `[p]applications settings`
2. Check Discord role permissions manually
3. Ensure the role denies view permissions for all channels except the application category

### Form fields aren't showing

1. Check configured fields: `[p]applications field list`
2. Ensure at least one field is configured
3. Verify the modal is opening (check Discord client compatibility)

### Can't approve/deny applications

1. Ensure you have "Manage Guild" permission
2. Check if the application exists: `[p]applications view @user`
3. Verify the application status is "pending"

## Support

If you encounter any issues or have questions, contact the cog author on Discord: `wzyss`
