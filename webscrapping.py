import json
import re
import time
import requests
from bs4 import BeautifulSoup

uber_prefix = 'https://www.ubereats.com'
uber_braga  = 'https://www.ubereats.com/pt-en/city/braga-norte'



# links dos restaurantes que encontrei
restaurantes = []

#for i in range(3):
#    newLink = uber_braga + "?page=" + str(i)
#
#    page = requests.get(newLink)
#
#    pagina_restaurantes = re.findall('/pt-en/store/[^"]+', page.text)
#
#    for r_link in pagina_restaurantes:
#        restaurantes.append(uber_prefix + r_link)
#

print(restaurantes)


teste = 'https://www.ubereats.com/pt-en/store/camada-francesinhas-braga-caranda/ATC0LW-BSLybCCCntoKfww'

page = requests.get(teste)

#menus_titulos = re.findall(r'<h1 data-testid="menu-item-title" class="_iw _ix _bm _bk _al _bc">([^\<]*)</h1>', page.text)
menus_titulos = re.findall(r'menu-item-title', page.text)

menus_descricao = re.findall('', page.text)

print(menus_titulos)
# nome do menu
#<h1 data-testid="menu-item-title" class="_iw _ix _bm _bk _al _bc">Menu Mix</h1>
# descriçao
#<div class="_bo _el _bq _dt _w3 _w4 _bu"></div>
