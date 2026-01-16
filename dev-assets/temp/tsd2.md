# 1.0-Authentication
Created Monday 20 October 2025

Here's a **technical specification** for implementing a **generic OAuth token utility in Salesforce Commerce Cloud (SFCC)** to authenticate with the third-party system **Fluent Connect** using the **Client Credentials Grant** flow. This utility will cache the token and handle automatic refresh when the token expires.

--------------------

## 🔧 Technical Specification: Fluent Connect OAuth Token Utility for SFCC

### **Objective**
Implement a reusable utility in Salesforce Commerce Cloud (SFCC) to authenticate with Fluent Connect using OAuth 2.0 Client Credentials Grant. The utility will:
* Request and store the access token in SFCC's cache.
* Automatically refresh the token when expired.
* Provide the token to other services needing Fluent Connect authentication.

--------------------

### **Authentication Details**

**Endpoint:**
'''
POST https://{HIBDEV}.sandbox.api.fluentretail.com/oauth/token
'''

**Query Parameters:**
* `username={username}`
* `password={pwd}`
* `client_id={id}`
* `client_secret={secret}`
* `grant_type=password`
* `scope=api`

**Headers:**
'''
Accept: application/json
'''

**Response:**
'''
{
	"access*token": "4eN4Aq2JEBqGGHWoRkVsmmu*vOSg",
	"token_type": "bearer",
	"expires_in": 86399,
	"scope": "api",
	"Roles": [],
	"LastName": "",
	"User_id": 1,
	"FirstName": "nal7demo"
}
'''

--------------------

### Metadata
To organize all Fluent Connect service variables under a **custom attribute grouping** in Salesforce Commerce Cloud, you’ll want to use **Custom Preferences** defined in **Business Manager > Administration > Sites > Manage Sites > [Your Site] > Preferences > Custom Preferences**.

Here’s how to lay it out:

--------------------

## 🗂️ Custom Preference Group: `FluentConnect`

### Group ID: `FluentConnect`
### Group Name: Fluent Connect Integration
### Description: Stores credentials and configuration for Fluent Connect OAuth token service.

--------------------

### 🔐 Preferences in the Group
| **Preference ID**              | **Description**                                                        | **Dev/Test Value** | **Prod Value** |
|--------------------------------|------------------------------------------------------------------------|--------------------|----------------|
| `FluentConnect_Username`     | Username used to authenticate with Fluent Connect                      |                    |                |
| `FluentConnect_Password`     | Password used to authenticate with Fluent Connect                      |                    |                |
| `FluentConnect_ClientID`     | OAuth client ID for Fluent Connect                                     |                    |                |
| `FluentConnect_ClientSecret` | OAuth client secret for Fluent Connect                                 |                    |                |
| `FluentConnect_Environment`  | Environment prefix (e.g., `demoenv`, `prodenv`)                    |                    |                |
| `FluentConnect_Domain`       | Domain for Fluent Connect API (e.g., `sandbox.api.fluentretail.com`) |                    |                |

Business Manager import:
[:2-Projects:HibbettOMS:FluentConnect:1.0-Authentication:importfile|+importfile]

### **Design Components**
Fluent Connect tokens will be cached using a custom cache in SFCC:

This will be a **code level** construct but viewable and manageable in Operations / ![](./.\pasted_image_251020.png)ng)ng)ng)ng)ng)

#### 1. **Token Cache Key**
'''
const TOKEN*CACHE*KEY = 'FluentConnectAuthToken';
'''

#### 2. **Custom Utility Module**
Create a reusable module: `fluentConnectToken.js`

##### **Exports:**
* `getToken()`: Returns a valid token, refreshing if needed.
* `refreshToken()`: Performs the token request and updates the cache.

--------------------

### **Implementation Steps**

#### ✅ 1. **Token Retrieval Logic**
'''
function getToken() {
	const CacheMgr = require('dw/system/CacheMgr');
	const tokenCache = CacheMgr.getCache('FluentConnectTokenCache');
	let tokenData = tokenCache.get(TOKEN*CACHE*KEY);

	if (tokenData && !isTokenExpired(tokenData)) {
		return tokenData.access_token;
	}

	// Token is missing or expired
	tokenData = refreshToken();
	return tokenData ? tokenData.access_token : null;
}
'''

#### ✅ 2. **Token Refresh Logic**
'''
function refreshToken() {
	const LocalServiceRegistry = require('dw/svc/LocalServiceRegistry');
	const tokenService = LocalServiceRegistry.createService('fluent.connect.token', {
		createRequest: function (svc) {
			svc.setRequestMethod('POST');
			svc.addHeader('Accept', 'application/json');
			svc.setURL('https://{HIBDEV}.sandbox.api.fluentretail.com/oauth/token?' +
				'username={username}&password={pwd}&client*id={id}&client*secret={secret}&grant_type=password&scope=api');
			return '';
		},
		parseResponse: function (svc, response) {
			return JSON.parse(response.text);
		},
		filterLogMessage: function (msg) {
			return msg;
		}
	});

	const result = tokenService.call();
	if (result.ok && result.object && result.object.access_token) {
		const tokenData = {
			access*token: result.object.access*token,
			expires*at: Date.now() + (result.object.expires*in * 1000)
		};

		const CacheMgr = require('dw/system/CacheMgr');
		const tokenCache = CacheMgr.getCache('FluentConnectTokenCache');
		tokenCache.put(TOKEN*CACHE*KEY, tokenData);

		return tokenData;
	}

	return null;
}
'''

#### ✅ 3. **Token Expiry Check**
'''
function isTokenExpired(tokenData) {
	return !tokenData.expires*at || Date.now() >= tokenData.expires*at;
}
'''

[+fluentConnectToken.js](./+fluentConnectToken.js)

--------------------

### **Configuration**

#### 🔐 **Service Definition**
Create a service in **BM > Administration > Services**:
 */*/ID*//: `fluent.connect.token`
 */*/Type*//: HTTP
 */*/Credential*//: Leave blank (credentials are passed in query string)
 */*/URL*//: Use placeholder and override in code or via site preferences

--------------------

### **Usage Example**
'''
const fluentTokenUtil = require('*/cartridge/scripts/utils/fluentConnectToken');
const token = fluentTokenUtil.getToken();

// Use token in Fluent Connect API calls
'''

--------------------

FluentConnectTokenTest.ds
Here's a **test script** you can use in your SFCC cartridge to validate the `fluentConnectToken` utility. This script can be placed in a controller or executed via a job or pipeline for testing purposes.

--------------------

### 📄 `FluentConnectTokenTest.ds`

'''
'use strict';

var server = require('server');
var fluentConnectToken = require('*/cartridge/scripts/utils/fluentConnectToken');

server.get('Start', function (req, res, next) {
	var token = fluentConnectToken.getToken();

	if (token) {
		res.json({
			success: true,
			message: 'Token retrieved successfully.',
			token: token
		});
	} else {
		res.json({
			success: false,
			message: 'Failed to retrieve token.'
		});
	}

	next();
});

module.exports = server.exports();
'''

--------------------

### 🧪 How to Use

1. **Place the script** in your cartridge under:
'''
   int*fluent*connect/cartridge/controllers/FluentConnectTokenTest.js
'''

2. **Access the endpoint** in your browser:
'''
   https://<your-site>/on/demandware.store/Sites-<siteID>-Site/default/FluentConnectTokenTest-Start
'''

This will return a JSON response with the token and a success message.

Would you like me to generate a mock job script to test this utility on a schedule instead?

### **Best Practices**
 *Use site-specific cache** if tokens vary by site.
 *Store credentials in Custom Preferences**.
 *Add logging and error handling** for failed token refresh attempts.
* Suggested cartridge structure

'''
int*fluent*connect
└── cartridge/
	├── scripts/
	│   ├── utils/
	│   │   └── fluentConnectToken.js       # Token management utility
'''

 */*/`fluentConnectToken.js`*//: Contains logic for token retrieval, caching, and refresh.


Here are **recommended specifications** for configuring the **Service Circuit Breaker and Timeout settings** in Salesforce Commerce Cloud (SFCC) for the Fluent Connect token service (`fluent.connect.token`):

--------------------

## 🔧 SFCC Service Configuration: Circuit Breaker & Timeout

### **1. Timeout Settings**
These define how long SFCC waits for a response before failing the request.

| Setting                | Recommended Value | Notes                                   |
|------------------------|-------------------|-----------------------------------------|
| **Connection Timeout** | `5 seconds`     | Time to establish TCP connection.       |
| **Socket Timeout**     | `10 seconds`    | Time to wait for data after connection. |
| **Execution Timeout**  | `15 seconds`    | Total time allowed for the request.     |

> These values balance responsiveness with tolerance for occasional latency. You can adjust based on Fluent Connect's SLA and observed performance.

--------------------

### **2. Circuit Breaker Settings**
These protect SFCC from repeated failures by temporarily disabling the service after a threshold is reached.

| Setting                 | Recommended Value  | Notes                                                   |
|-------------------------|--------------------|---------------------------------------------------------|
| **Enabled**             | `true`           | Activates the circuit breaker.                          |
| **Minimum Calls**       | `5`              | Minimum number of calls before evaluating failure rate. |
| **Error Threshold (%)** | `50`             | If 50% of calls fail, the breaker trips.                |
| **Reset Interval (ms)** | `30000` (30 sec) | Time before retrying the service after tripping.        |

> These settings help prevent cascading failures and allow graceful recovery.

--------------------

### **3. Retry Strategy (Optional)**
If Fluent Connect occasionally fails due to transient issues, you can configure retries:

| Setting         | Recommended Value | Notes                      |
|-----------------|-------------------|----------------------------|
| **Max Retries** | `2`             | Number of retry attempts.  |
| **Retry Delay** | `1000 ms`       | Wait time between retries. |

--------------------

### 🛠 How to Apply
These settings are configured in **Business Manager** under:

**Administration → Operations → Services → Service Name → Settings Tab**

Would you like a sample `services.json` snippet with these settings included for deployment?