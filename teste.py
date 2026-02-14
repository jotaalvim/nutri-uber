from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time
import json
import os

def scrape_ubereats_menu(url):
    menu_data = []
    with sync_playwright() as p:
        # Launch browser. Set headless=True to run in background
        # Use 'chrome' channel to look more like a real browser if Chrome is installed
        try:
            browser = p.chromium.launch(headless=True, channel="chrome")
        except:
            browser = p.chromium.launch(headless=True)
            
        context = browser.new_context(
            # Mimic a real user agent to reduce the chance of being blocked
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            device_scale_factor=2,
            locale="pt-PT",
            timezone_id="Europe/Lisbon"
        )
        
        # Add stealth scripts to hide automation
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = context.new_page()
        
        # Add extra headers
        page.set_extra_http_headers({
            "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Referer": "https://www.google.com/"
        })
        
        print(f"Navigating to {url}...")
        page.goto(url)

        # Wait for the page to load the menu items. 
        # You may need to adjust the selector to match Uber Eats' current layout
        try:
            # Wait for content to load
            # Increased timeout to allow for manual CAPTCHA solving
            page.wait_for_load_state("networkidle", timeout=30000)
            
            # Scroll down slowly to trigger lazy loading of images and items
            for i in range(5):
                page.mouse.wheel(0, 1000)
                time.sleep(1)
        except Exception as e:
            print(f"Page took too long to load or CAPTCHA blocked the request: {e}")
            # Continue anyway as we might have partial content

        # Grab the fully rendered HTML
        html = page.content()
        browser.close()
        
        # Save HTML for debugging
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Page HTML saved to debug_page.html")

    # Parse the HTML with BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')

    # Find all menu item containers. 
    # Strategy 1: Look for <li> tags (common in lists)
    items = soup.find_all('li') 
    print(f"Strategy 1 (li) found: {len(items)} items")
    
    # Strategy 2: Look for role="listitem"
    if not items:
         items = soup.find_all('div', {"role": "listitem"})
         print(f"Strategy 2 (role=listitem) found: {len(items)} items")

    # Strategy 3: Heuristic - Find items by Price symbol '€'
    # This is often the most robust way if classes are obfuscated.
    if not items or len(items) < 5: # If we found very few items, try this
        print("Trying Strategy 3: Price Heuristic...")
        prices = soup.find_all(string=lambda text: text and '€' in text)
        potential_items = []
        seen_parents = set()
        
        for price_text in prices:
            # Go up 3-4 levels to find the card container
            current = price_text.parent
            for _ in range(4):
                if current and current.name in ['li', 'div', 'a']:
                    # Simple check: does this container have a header inside?
                    if current.find(['h3', 'h4', 'span']) and current not in seen_parents:
                        potential_items.append(current)
                        seen_parents.add(current)
                        break # Found a container for this price
                current = current.parent if current else None
        
        if len(potential_items) > len(items):
            items = potential_items
            print(f"Strategy 3 (Prices) found: {len(items)} unique items")

    for item in items:
        # Extract Name (often in an <h3>, <span>, or a specific div)
        # Try finding headers first for more accuracy
        name_elem = item.find(['h3', 'h4', 'h5', 'h6'])
        if not name_elem:
             name_elem = item.find('span', style=True) # Sometimes titles have inline styles
        if not name_elem:
             name_elem = item.find('span')
        
        # Extract Description
        # Description is usually a p tag or a div with specific class but avoiding the title itself.
        # We can try to find a paragraph that is NOT the name.
        desc_elem = item.find('p')
        if not desc_elem and name_elem:
             # Look for a div that has text but is different from name_elem
             divs = item.find_all('div')
             for div in divs:
                 if div.text.strip() and div.text.strip() != name_elem.text.strip():
                     desc_elem = div
                     break
        
        # Extract Image
        # Uber Eats often uses <picture> tags or lazy-loaded <img> tags
        img_elem = item.find('img')

        # Extract Price
        # Look for the first text node containing '€' (or other currency symbols if generic)
        price = None
        # Heuristic: Find first text that looks like a price
        for string in item.stripped_strings:
            if '€' in string:
                price = string
                break
        
        # Extract Product Link
        product_url = None
        link_elem = item.find('a')
        if link_elem and link_elem.get('href'):
            href = link_elem.get('href')
            if href.startswith('/'):
                 product_url = f"https://www.ubereats.com{href}"
            else:
                 product_url = href

        name = name_elem.text.strip() if name_elem else None
        description = desc_elem.text.strip() if desc_elem else None
        
        # Filter out self-referencing descriptions
        if name and description and name == description:
            description = None

        # Images on dynamic sites often use 'src' or a lazy-loading attribute like 'srcset'
        image_url = None
        if img_elem:
            image_url = img_elem.get('src') or img_elem.get('srcset')

        # Only append if it actually looks like a menu item (has a name)
        # Avoid FAQs which usually start with "Can I", "How do I", etc.
        if name and not name.startswith(("Can I", "How do I", "Where can I", "Is ")):
            menu_data.append({
                "name": name,
                "description": description,
                "price": price,
                "image_url": image_url,
                "product_url": product_url
            })

    return menu_data

def save_to_json_append(restaurant_entry, filename="all_menus.json"):
    """
    Appends a SINGLE restaurant object to a JSON file.
    It handles reading the existing list and appending, or creating a new list.
    """
    if not restaurant_entry:
        return

    data_list = []
    
    # Check if file exists and is not empty
    if os.path.exists(filename) and os.path.getsize(filename) > 0:
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data_list = json.load(f)
                if not isinstance(data_list, list):
                    # If for some reason it's not a list, wrap it or warn
                    print(f"Warning: {filename} content is not a list. Resetting to list.")
                    data_list = []
        except json.JSONDecodeError:
            print(f"Warning: Could not decode {filename}. Starting fresh.")
            data_list = []
    
    # Check for duplicates? For now just append.
    data_list.append(restaurant_entry)
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data_list, f, ensure_ascii=False, indent=2)
    
    print(f"Appended data to {filename}. Total restaurants: {len(data_list)}")

# Run the scraper
if __name__ == "__main__":
    # Settings
    input_file = "restaurantes.txt"
    output_file = "all_menus.json"
    
    # Read URLs from file
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found. Please create it with one URL per line.")
        exit(1)
        
    with open(input_file, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip()]
    
    total_urls = len(urls)
    
    print(f"Found {total_urls} restaurants to scrape in {input_file}")
    
    for idx, target_url in enumerate(urls, 1):
        # Fix possible malformed URLs
        if not target_url.startswith('http'):
             target_url = "https://" + target_url.lstrip('/')
             
        print(f"\n--- Processing {idx}/{total_urls} ---")
        print(f"URL: {target_url}")
        
        try:
            scraped_items = scrape_ubereats_menu(target_url)
            
            if scraped_items:
                print(f"Successfully scraped {len(scraped_items)} items.")
                
                # Prepare data structure for this restaurant
                restaurant_data = {
                    "url": target_url,
                    "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "items_count": len(scraped_items),
                    "menu": scraped_items
                }
                
                save_to_json_append(restaurant_data, output_file)
            else:
                print("No items found for this URL.")
                
        except Exception as e:
            print(f"Error scraping {target_url}: {e}")
        
        # Small pause between requests to be nice
        time.sleep(2)
