# TSD
Created Thursday 25 September 2025
[:2-Projects:HibbettOMS:ForterFraud:TSD:OpenQuestions|+OpenQuestions]

![](./.\pasted_image_250925.png)

## Vendor Description
The Forter cartridge adds the power of Forter’s new-generation fraud prevention to the Salesforce Commerce Cloud platform to meet the challenges faced by modern enterprise e-commerce. Only Forter provides fully automated, real-time Decision as a Service™ fraud prevention, backed by a 100% chargeback guarantee.

The system eliminates the need for rules, scores, or manual reviews, making fraud prevention friction-free. Every transaction receives an instant approve/decline decision, removing checkout friction and the delays caused by manual reviews.

The Forter cartridge provides fraud prevention that is invisible to buyers and empowers merchants with increased approvals, smoother checkout, and the near elimination of false positives — meaning more sales and happier customers.

Behind the scenes, Forter’s machine learning technology combines advanced cyber intelligence with behavioral and identity analysis to create a multi-layered fraud detection mechanism.
The result is best for online merchants, and best for online customers.

## Functional Overview
**The Forter cartridge** links your Salesforce Commerce Cloud platform to Forter's sophisticated fraud-fighting system. Each order is analyzed, and a real-time approve or decline decision is returned—covered by a full fraud chargeback guarantee. Merchants can configure the capture/void settings according to policy and preference. All decisions can be seen in the Salesforce Commerce Cloud platform, and merchants can view more details for each transaction within the Forter Decision Dashboard.

The Forter cartridge facilitates fraud decisioning during checkout for both registered and guest customers. When a customer places an order, a call is made to Forter to evaluate the transaction. If approved, the customer is directed to the thank-you page, and the order appears in Merchant Tools with a Forter Decision of **"Approved"** and status **"New."** If declined, the customer may see a declined message (based on configuration), and the order is logged with a Forter Decision of **"Declined"** and status **"Failed."** This flow validates Forter's integration and decisioning logic across successful and failed order scenarios.

When a registered customer logs in, adds payment methods, updates addresses, or modifies their wishlist, the Forter cartridge sends this data to Forter for validation. These interactions help Forter build a behavioral profile and assess fraud risk for future transactions.

The Forter cartridge supports syncing updated order statuses with Forter by allowing merchants to manually change order states (e.g., Completed, Cancelled) in Merchant Tools and trigger the **ForterOrderUpdate** job to send those changes. This ensures Forter maintains accurate post-transaction data for fraud monitoring and analytics.

When a customer checks out using an express payment method (such as PayPal Express), the Forter cartridge sends transaction data to Forter upon order placement. Forter evaluates the transaction for fraud, and the resulting decision—**Approved** or **Declined**—is displayed in the Forter Order view along with the corresponding order status.


## Sites Integrated
www.hibbett.com
www.hibbettkids.com

## Metadata



## Custom Logic
* custom fields... add the radial ones? 
3.3 Custom Code 
 
The Forter platform allows for custom attributes to be sent in the request. It is recommended that if you 
have attributes that are relevant for fraud detection, you should set them in the request objects generated 
in the cartridge

 will login validation be used
	cartridge allows, do we want to let forter police the login? There is NO individual site preference for it.
	Yes, Forter can act as a gatekeeper for login events.
	The login data is sent to Forter, and Forter returns a decision.
	You control what happens next — whether to allow, deny, or verify the login — based on that decision.
	This logic is customizable in your storefront code (both SiteGenesis and SFRA implementations are supported).

 do we care about forter decisions outside of checkout?  assuming no.
	examples:
	* Login
	* Customer sign up
	* Customer profile, billing, address edits
	If not, do we really need to wire these in ... or will the forter js collection suffice for needs on the forter end?
 
## Task List
The following tasks should be performed.

### Install Cartridge
Follow the installation instructions on the cartridge install guide for Version 25.0.0 
* Import the metadata
* Update cartridge paths
* Ignore any of the 'Login' or 'My Account' or 'Wishlist' steps, these will **NOT** be used.
* Follow the instructions for the 'pre-auth' fraud flow.

![](./.\pasted_image_251010-001.png)

The below screenshot and commented code below illustrate the recommended placement of the code within the handlePaymentTransaction function.

![](./.\pasted_image_251010.png)''

Finalize the Forter Permissions

Go to Administration > Organization > Roles & Permissions.

Choose the Administrator role and click on the Business Manager Modules tab.
Select your site from the Select Context section.
Select Forter modules and click Apply.

![](./.\pasted_image_251010-002.png)


### Configure Cartridge
Setup the site preferences.

* Forter Enabled - true
* Forter Cancel on Decline - false (this will not be used)
* Forter Auth Invoice on Approve - false
* Forter Show Declined Page - true
* Forter Secret Key - provided by Forter
* Forter Site Id - configured per site.
* Number of Weeks - for Job, use the default of 4.
* Force Forter Decision - DISABLED, unless testing later.
* versionAPI - Provided by Forter
* Forter Abuse Policy Settings Enabled - True
* Abuse Prevention Settings.
	* For now these will all be set to 'No Action'
	* See https://hibbett.atlassian.net/browse/OMSFCC-28


### Customize Cartridge
We will be calling Forter prior to Payment gateway auth.  Therefore if a DECLINE decision is received from Forter we will **NOT** call Adyen for a transaction but will instead fail the order.

Per Forter:
*Declined*

* *Cancel Order On Decline: YES - If this is enabled, then the order will be failed ~~and a request to void the order will be sent directly to the processor.~~ If the Show Decline Message option is also enabled, then the customer will be directed to a decline page with a customized message.* 
* *Cancel Order on Decline: NO - If the cancel order on decline is NOT selected, then the buyer will be directed to a “thank you” page and order will be placed (via the decision node with condition ForterResponse.JsonResponseOutput.processorAction === 'skipCapture’). but No funds capture will occur*

Customize the cartidge so that there is no VOID call made, this is not necessary.


### OCAPI Support/Mobile Apps
do stuff

### PWA Composable Support
do more stuff.

### Setup Jobs
do stuff.

## Jobs
int_forter/cartridge/scripts/jobs/ForterOrderUpdate.js

Order Update Job Overview
The order update job monitors changes to order statuses in Salesforce Commerce Cloud and sends updates to the appropriate Forter API endpoint via HTTPS. It is recommended to run this job every 6 hours to ensure timely synchronization.

In the Custom Site Preferences section, you can configure the parameter "Number of weeks"—which determines the time range (in weeks) that the job queries to identify orders for status updates. The default value is 4 weeks.

This queires for unprocessed orders:

`(custom.forterDecision = {0} OR custom.forterDecision = {1} OR custom.forterDecision = {2}) AND creationDate >= {3}", 'APPROVED', 'DECLINED', 'NOT_REVIEWED')`

And for each order a callback is made to Forter to update the status of the order in the Forter system.

## Volume/Frequency
This occurs for each and every order in checkout, upon PlaceOrder.

## Credentials
Provided by Forter Team

## Timeout Settings
Services should be configured with a 5 second (5000 ms).
They should have rate limiting enabled with a 5 second interval, and a 10 second millisecond value.

Circuit Breaker should be enabled on these services for a 10,000 rate limit millisecond and 5 calls.

'''
 <timeout-millis>5000</timeout-millis>
<rate-limit-enabled>true</rate-limit-enabled>
<rate-limit-calls>5</rate-limit-calls>
<rate-limit-millis>10000</rate-limit-millis>
<cb-enabled>true</cb-enabled>
<cb-calls>5</cb-calls>
<cb-millis>10000</cb-millis>
'''

## Developer Notes
