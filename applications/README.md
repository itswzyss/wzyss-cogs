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

#### List current fields

```
[p]applications field list
```

#### Add a new field

```
[p]applications field add <name> <label> <type> [required]

Types: text (short), paragraph (long), number
```

Examples:

```
[p]applications field add age "What is your age?" text True
[p]applications field add experience "Tell us about your experience" paragraph True
[p]applications field add discord "How long have you used Discord?" number False
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

3. **Review Process**:
   - Admins can view, approve, or deny applications
   - On approval: Restricted role is removed → User gains full access
   - On denial: User is notified but channel remains open for appeal

### Admin Commands

#### View Settings

```
[p]applications settings
```

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
- Approval/denial information

Application data is retained until:
- The user leaves the server (automatic cleanup)
- An admin manually closes the application channel
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
