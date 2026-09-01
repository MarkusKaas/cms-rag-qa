# Umbraco CMS — Developer Reference

## What is Umbraco?

Umbraco is an open-source .NET CMS (Content Management System) built on ASP.NET Core.
It powers over 700,000 websites worldwide, ranging from small business sites to large enterprise portals.
Umbraco is highly flexible: editors manage content through the Umbraco backoffice (a web-based UI),
while developers customise the front-end using Razor views, controllers, and the Delivery API.

---

## Content Architecture

### Document Types

A **Document Type** defines the schema for a piece of content — similar to a class in object-oriented programming.
Each Document Type contains one or more **Property Editors** (fields).

Common built-in Property Editors:

| Editor                  | Stored Value      | Use Case                          |
|-------------------------|-------------------|-----------------------------------|
| Textstring              | string            | Short single-line text            |
| Textarea                | string            | Multi-line plain text             |
| Rich Text Editor (RTE)  | HTML string       | Formatted body content            |
| Media Picker            | UDI reference     | Images, videos, files             |
| Content Picker          | UDI reference     | Internal links between pages      |
| Dropdown                | string / value    | Fixed-choice fields               |
| True/False              | boolean           | Toggle/flag fields                |
| Date/Time               | ISO 8601 string   | Publish dates, event dates        |
| Block List Editor       | JSON              | Structured repeating content      |
| Block Grid Editor       | JSON              | Grid-based layout blocks          |

### Templates and Compositions

- **Template**: A Razor `.cshtml` view file bound to one or more Document Types.
- **Composition**: A reusable set of properties (like a mixin) that can be shared across multiple Document Types.
  For example, an `SEO Composition` adding Meta Title and Meta Description to any Document Type.

### Content Tree

The content tree mirrors the site URL structure. Each node is a **page** with:
- A Document Type (schema)
- Property values (the actual content)
- Publish/unpublish state
- A unique **UDI** (Uniform Data Identifier) used for cross-referencing

---

## Umbraco Delivery API

Umbraco 10+ includes a headless **Delivery API** that exposes content as JSON.

### Base URL

```
GET /umbraco/delivery/api/v2/content
```

### Fetch a single page by route

```
GET /umbraco/delivery/api/v2/content/item/?route=/about-us
```

### Fetch by Document Type

```
GET /umbraco/delivery/api/v2/content?filter=contentType:articlePage&skip=0&take=10
```

### Authentication

Public content: no auth required (if the Delivery API is set to public access).
Protected content: requires a `StartItem` header or Bearer token from the Members API.

---

## Content Modelling Best Practices

1. **Keep Document Types focused.** One Document Type per distinct page/component type.
   Avoid "mega" Document Types with 30+ properties.
2. **Use Compositions for shared fields.** SEO fields, hero images, and call-to-action blocks are
   good candidates for compositions.
3. **Prefer Block List over Nested Content.** Nested Content is deprecated in Umbraco 13+.
   Block List and Block Grid are the recommended alternatives.
4. **Name properties clearly.** Use Pascal Case for aliases (e.g. `heroHeading`, `bodyText`).
5. **Add descriptions to properties.** Editors see these in the backoffice — clear descriptions
   reduce support requests.

---

## Umbraco AI Integration Patterns

### Pattern 1 — AI-Assisted Meta Description Generation

Use the Claude API to auto-generate SEO meta descriptions from page body content.

**Implementation sketch:**
1. Listen to the `ContentSavingNotification` in a Notification Handler.
2. Read the page's `bodyText` property.
3. Call the Anthropic Messages API with a prompt like:
   *"Write a 155-character SEO meta description for the following content: {bodyText}"*
4. Set the `metaDescription` property to the returned string.
5. Allow editors to override the generated value.

### Pattern 2 — Semantic Search over CMS Content

Index Umbraco content in a vector database for semantic Q&A.

**Implementation sketch:**
1. Export content via the Delivery API.
2. Chunk each page's `bodyText` (e.g. 800 words, 150-word overlap).
3. Embed chunks using `sentence-transformers/all-MiniLM-L6-v2`.
4. Store embeddings in ChromaDB with metadata: `{pageId, documentType, url}`.
5. On user query: embed the query → cosine search → retrieve top-k chunks → generate answer with Claude.

### Pattern 3 — Content Quality Checker

Validate editorial content before publish.

**Checks to implement:**
- Reading level (Flesch-Kincaid score below 60 → warn)
- Meta description length (< 50 or > 160 characters → warn)
- Missing alt text on images → error
- Broken internal links (Content Picker pointing to unpublished nodes) → error

---

## Umbraco Versions Quick Reference

| Version | .NET Version | Status            | Key Feature                     |
|---------|-------------|-------------------|---------------------------------|
| 8.x     | .NET 4.7.2  | End of Life       | Last non-Core version           |
| 10 LTS  | .NET 6      | Security patches  | Delivery API (preview)          |
| 12      | .NET 7      | End of Life       | Full Delivery API v1             |
| 13 LTS  | .NET 8      | Supported         | Block Grid, Nested Content gone |
| 14      | .NET 8      | Supported         | New backoffice (Bellissima/Lit)  |
| 15      | .NET 9      | Supported         | Backoffice extensions stable     |

---

## Mailchimp API Integration with Umbraco

A common pattern for Vejle Kommune: sync newsletter subscribers from the Umbraco Members section
to a Mailchimp audience.

### Flow

1. Editor creates a Mailchimp-synced Member Group in Umbraco (e.g. "Newsletter subscribers").
2. When a Member is added to the group, a **Notification Handler** fires on `MemberGroupNotification`.
3. The handler calls the Mailchimp Marketing API:

```csharp
POST https://us1.api.mailchimp.com/3.0/lists/{listId}/members
{
  "email_address": member.Email,
  "status": "subscribed",
  "merge_fields": {
    "FNAME": member.Name
  }
}
```

4. Mailchimp returns the subscriber's `id` — store this in a custom Member property for future updates/unsubscribes.

### Unsubscribe handling

When a Member is removed from the group:
```csharp
PATCH https://us1.api.mailchimp.com/3.0/lists/{listId}/members/{subscriberHash}
{ "status": "unsubscribed" }
```

The `subscriberHash` is the MD5 hash (lowercase) of the member's email address.

---

## Frequently Asked Questions

**Q: How do I get the current page's Document Type alias in a Razor view?**
A: `@Model.ContentType.Alias`

**Q: How do I get a typed property value in a Razor view?**
A: `@Model.Value<string>("heroHeading")` — or use ModelsBuilder: `@Model.HeroHeading`

**Q: What is the difference between `Url()` and `UrlAbsolute()`?**
A: `Url()` returns a root-relative path (`/about-us`). `UrlAbsolute()` returns the full URL including domain (`https://vejle.dk/about-us`).

**Q: How do I query child pages of the current node?**
A: `@Model.Children()` returns all published child nodes. Filter by Document Type:
`@Model.Children().Where(c => c.ContentType.Alias == "articlePage")`

**Q: Can I use the Delivery API without enabling it globally?**
A: Yes — you can restrict it to specific content by configuring protected access and using the `ApiKey` middleware.
