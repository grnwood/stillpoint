# {{PageName}} (PWA Kit Version)
Created {{DayDateYear}}
{{DOW}}, {{Month}} {{dd}} {{YYYY}}

---

## SOW Description
Summarize the deliverable with respect to the PWA Kit storefront. Note any requirements specific to the Retail React App, Commerce API usage, or custom proxy middleware.

---

## Business Context & Goals
Explain why this feature is needed and how it impacts shopper experience, performance, and KPIs in a headless storefront.

---

## Functional Overview
Describe the feature behavior within the PWA Kit application. Focus on shopper interactions, UI flow, and expected outcomes.

---

## User Journeys / Use Cases

- Primary Flow:
- Alternative Flow(s):
- Error / Edge Cases:

---

## Sites, Environments & API Config
Identify impacted sites, .env variables, and Commerce API endpoints.

- Sites:
- Locales:
- Environments: Sandbox / Staging / Production
- Required OCAPI / B2C Commerce API Endpoints:
- PWA Kit Environment Variables:

---

## Dependencies & Assumptions

### Assumptions
-

### Dependencies
-

---

## Metadata & Data Model
Document any data passed through Commerce API, custom proxy, or React state.

| Object / Model | Field | Type | Description | Required? |
|----------------|--------|------|-------------|-----------|
|                |        |      |             |           |

---

## Site Preferences (Backend)
List backend preferences that toggle PWA behavior.

| ID | Name | Type | Description | Default |
|----|------|------|-------------|---------|
|    |      |      |             |         |

---

## PWA Components & UI Changes

- Components to modify/create:
- Routes to add/update:
- Hooks / Custom React Utilities:
- Error states:
- Loading states:
- Accessibility considerations:

---

## Performance Considerations (Critical for PWA)

- API request minimization strategy
- Caching (Commerce API, SWR, HTTP)
- Client bundle impact
- Edge caching / CDN behavior

---

## Integration Overview

- External system:
- Proxy service paths:
- Required middleware:
- Data transformation requirements:

---

## Credentials & Endpoints

| Environment | Endpoint URL | Auth Method | Notes |
|-------------|--------------|-------------|-------|
| Test        |              |             |       |
| Prod        |              |             |       |

---

## Timeout & Retry Strategy

- Client-side retry rules
- Server-side proxy retry logic
- Timeout thresholds

---

## Logging & Monitoring

- Client-side logs (console, error boundaries)
- Proxy logs (server middleware)
- Monitoring hooks (New Relic, logs in SLAS)

---

## Availability & Fallback Behavior

- Offline behavior
- API failure fallback behavior
- User-facing message strategy

---

## Security & Compliance

- PCI implications (ensure all payment flows remain PCI-compliant)
- PII handling rules
- Token handling in PWA Kit

---

## Developer Notes

- Patterns to follow
- Cartridge interactions
- React coding guidance
- Testing strategies (Jest + Testing Library)

---

## Expected Task List

- [ ] Update/Introduce components
- [ ] Add/Update route definitions
- [ ] Implement proxy service
- [ ] Add environment variables
- [ ] Update .env templates
- [ ] Add unit & integration tests
- [ ] Document API usage

---

## Request Mapping
Describe how UI inputs map into proxy requests and backend APIs.

---

## Response Mapping
Describe how API responses map into React state, components, and views.

---

## Error Handling & Fallback
Define handling for:

- Timeout behavior
- API failures
- Degraded mode
- Shopper messaging

---

## Screenshots & Diagrams
(Add component mocks, flow diagrams, or architecture visuals here.)
