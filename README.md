# nutri-uber
era top fazer um puglin de vscode pra mandar vir comida saudável

## Seed basket cache

Pre-populate the API cache with healthy ready-to-go baskets (no scraping needed):

```bash
python seed_basket_cache.py
```

## Uber Eats integration (add basket to cart)

Assumes you're already logged into Uber Eats in Chrome. Uses your existing browser via CDP.

1. Close Chrome, then start with: `open -a "Google Chrome" --args --remote-debugging-port=9222` (Mac)
2. Set `CHROME_CDP_URL=http://localhost:9222` and run `python api.py`
3. Add to cart opens a new tab in your Chrome and adds items

   ```bash
   curl -X POST http://localhost:5001/add_basket_to_cart \
     -H "Content-Type: application/json" \
     -d '{"store_url":"https://...", "items":[{"name":"Peito de frango grelhado"}]}'
   ```
