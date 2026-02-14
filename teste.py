from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time

def scrape_ubereats_menu(url):
    menu_data = []

    with sync_playwright() as p:
        # Launch browser. Set headless=False to see the browser or solve CAPTCHAs manually.
        # Set to True for headless execution.
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            # Mimic a real user agent to reduce the chance of being blocked
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print(f"Navigating to {url}...")
        page.goto(url)

        # Wait for the page to load the menu items. 
        # You may need to adjust the selector to match Uber Eats' current layout
        try:
            # Wait for content to load
            page.wait_for_load_state("networkidle", timeout=15000)
            
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

    # Parse the HTML with BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')

    # Find all menu item containers. 
    # TIP: Right-click a menu item on the website, click "Inspect", and find the common wrapper tag/class.
    # Uber Eats often uses <li> tags for individual items inside a <ul> list.
    items = soup.find_all('li') 
    
    # If no items found, try another selector for menu items (e.g. divs with specific classes or roles)
    if not items:
         items = soup.find_all('div', {"role": "listitem"})

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
                "image_url": image_url
            })

    return menu_data

# Run the scraper
if __name__ == "__main__":
    target_url = "https://www.ubereats.com/pt-en/store/camada-francesinhas-braga-caranda/ATC0LW-BSLybCCCntoKfww"
    scraped_items = scrape_ubereats_menu(target_url)

    for idx, item in enumerate(scraped_items, 1):
        print(f"--- Item {idx} ---")
        print(f"Name: {item['name']}")
        print(f"Description: {item['description']}")
        print(f"Image: {item['image_url']}\n")
