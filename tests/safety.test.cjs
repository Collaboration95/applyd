const test = require('node:test');
const assert = require('node:assert/strict');
const { detectAccountSafety, extractListing } = require('../scripts/extract_linkedin.js');
test('current-page safety challenges are typed and stop extraction', () => {
  for (const [text,kind] of [['Please complete CAPTCHA','captcha'],['Unusual activity on your account','unusual_activity'],['Too many requests','rate_limit'],['Your account is restricted','restriction'],['Sign in to continue','sign_in']]) {
    assert.equal(detectAccountSafety(text),kind);
    assert.throws(() => extractListing({body:{innerText:text},URL:'https://www.linkedin.com/jobs/view/900001'}),error => error.stop_reason === 'account_safety');
  }
  assert.equal(detectAccountSafety('Synthetic role details. Navigation: Sign in'), null);
});
