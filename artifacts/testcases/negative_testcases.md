# Negative Test Cases

- Generated at: 2026-09-10T01:18:33

## NEC-TC-001
- Title: Invalid Mobile Number During Registration
- Description: Enter a 9-digit mobile number (e.g.
- Expected Result: System displays a validation error message indicating the mobile number must be exactly 10 digits and does not proceed to send an OTP

## NEC-TC-002
- Title: Incorrect OTP Entry During Verification
- Description: Enter an incorrect OTP value (e.g.
- Expected Result: System displays an OTP verification failed or incorrect OTP error message and does not navigate to the account creation form

## NEC-TC-003
- Title: Non-Existent Pin Code in Location Search
- Description: Enter a non-existent pin code (e.g.
- Expected Result: System displays a no results found or invalid pin code message and does not apply any location to the account

## NEC-TC-004
- Title: Alphanumeric Characters in Pin Code Field
- Description: Enter non-numeric alphanumeric characters (e.g.
- Expected Result: System displays a validation error for invalid pin code format or rejects non-numeric input and does not return location results

## NEC-TC-005
- Title: Invalid Email Format in Deal of the Day Share
- Description: Click the share or email button for the Deal of the Day product and enter an invalid email address format (e.g.
- Expected Result: System displays an email format validation error and does not send the product image

## NEC-TC-006
- Title: Sharing Deal of the Day with Unloaded Product Data
- Description: Click the share or email button for the Deal of the Day product when the product image
- Expected Result: System displays an error message indicating product data is unavailable or disables the share option until all product data is successfully loaded

## NEC-TC-007
- Title: Reversed Price Range Filter Values
- Description: Click on the price range filter and set the minimum price to 727 and the maximum price to 427 (minimum greater than maximum)
- Expected Result: System displays a validation error indicating the price range is invalid or shows no results for the reversed range and does not filter products

## NEC-TC-008
- Title: Zero or Negative Bargain Offer Amount
- Description: Click the Bargain button on the selected product
- Expected Result: System displays a validation error indicating the offer amount must be a positive number and does not submit the bargain offer to the seller

## NEC-TC-009
- Title: Insufficient Balance on Bank Portal During Net Banking Payment
- Description: Select Net Banking as payment method
- Expected Result: System displays a payment failed or insufficient balance error message

## NEC-TC-010
- Title: Abandoning Payment Without Completing on Bank Portal
- Description: Select Net Banking
- Expected Result: System does not display the order placed successfully message
