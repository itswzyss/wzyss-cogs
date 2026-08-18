# ListingInterest

**Short description:** Interest buttons on product listings that open private channels and notify sellers.

## Description

Attach an “I’m interested” button to product listings without a storefront. Admins can either have the bot **reply** to a human-posted listing with a button message, or **bind** a button onto a bot-owned message (e.g. after reposting with an embed cog).

When a member clicks the button, the bot creates a private interest channel under a configured category, grants access to the buyer and configured notify targets, optionally pings users/roles in that channel, and optionally DMs configured users (and members of configured roles, up to a safety cap). Managers can close the channel with a button.

## Install

```
[p]cog install wzyss-cogs listinginterest
```

## Requirements

None.

## Tags

listings, sales, utility, moderation

## Setup (admin only)

1. **Category:** `[p]listinginterestset category <category>` – Where interest channels are created.
2. **Notify (optional):**
   - `[p]listinginterestset pingusers @User1 @User2` – Ping these users in the new channel.
   - `[p]listinginterestset pingroles @Role1` – Ping these roles in the new channel.
   - `[p]listinginterestset dmusers @User1` – DM these users when interest is expressed.
   - `[p]listinginterestset dmroles @Role1` – DM members of these roles (expanded up to the DM cap).
3. **Managers (optional):** `[p]listinginterestset managerroles @Role` – Who can close interest channels (plus Manage Guild / admins).
4. **Defaults (optional):** `[p]listinginterestset buttonlabel I'm interested`, `[p]listinginterestset dmcap 25`.
5. **Attach a listing:**
   - Human post: `[p]listinginterest reply <message_id_or_link> [label]`
   - Bot-owned post: `[p]listinginterest bind <message_id_or_link> [label]`

## Commands

### Attach / manage listings

- `[p]listinginterest` (`[p]li`) – Show subcommands.
- `[p]listinginterest reply <message_id_or_link> [label]` – Reply to a listing with an interest button.
- `[p]listinginterest bind <message_id_or_link> [label]` – Add an interest button to a bot-owned message.
- `[p]listinginterest unbind <listing_id|message_id_or_link>` – Remove the button config (and clear the view on the bot message). Does not delete the original human listing.
- `[p]listinginterest list` – List configured listings.

### Configuration

- `[p]listinginterestset` (`[p]liset`) – Show config subcommands.
- `[p]listinginterestset category <category>` – Set interest channel category.
- `[p]listinginterestset buttonlabel <label>` – Default button label.
- `[p]listinginterestset pingusers [users...]` – Set or clear ping users.
- `[p]listinginterestset pingroles [roles...]` – Set or clear ping roles.
- `[p]listinginterestset dmusers [users...]` – Set or clear DM users.
- `[p]listinginterestset dmroles [roles...]` – Set or clear DM roles (members expanded).
- `[p]listinginterestset dmcap <n>` – Max members DMed per DM role (default 25).
- `[p]listinginterestset managerroles [roles...]` – Set or clear manager roles.
- `[p]listinginterestset settings` – Show current settings.

## User flow

- Member clicks **I’m interested** on a listing button message.
- Bot creates a private channel and confirms with an ephemeral link.
- Configured users/roles are pinged and/or DMed with the listing link and channel.
- One open interest channel per buyer per listing; other buyers can still open their own.
- Managers use **Close** to delete the channel.

## Notes

- Discord cannot DM a role; DM roles are expanded to current members (capped).
- Binding requires a message authored by the bot. For human listings, use `reply` (or repost with another cog, then `bind`).
