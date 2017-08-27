import requests, urllib2
import re, glob, os, argparse

def getLinks(site, prefix):
    pat = r'href="(.+?)"'
    links = re.finditer(pat, site)
    links_list = []
    for link in links:
        ln = link.group(1)
        if 'http' not in ln:
            ln = prefix + ln
        if 'reducing' in ln or 'foundational' in ln:
            links_list.append(ln)
    return links_list

def strvar(i):
    s = str(i)
    if i <= 9:
        s = '00'+s
    if (i >= 10) and (i <= 99):
        s = '0'+s
    return s

def compileREs(page):
    pats = {}
    pat = re.compile(r'<title.*?title>', flags=re.M|re.DOTALL)
    pats['title'] = pat
    if page == 'rs':
        # Initial comments
        pat = re.compile(r'^.*?<html lang="en-US">', flags=re.DOTALL)
        pats['comments'] = pat

        # Side bar
        pat = re.compile(r'<aside id=.*?<\/aside>', flags=re.DOTALL)
        pats['side'] = pat

        # Remove bottom of page (contact and social media)
        pat = re.compile(r'<div id="header-text-nav-container">.*?header-text-nav-container -->', flags=re.DOTALL)
        pats['bottom'] = pat

        # Remove footer
        pat = re.compile(r'<footer id.*?<\/footer>', flags=re.DOTALL)
        pats['footer'] = pat

    if page == 'fri':
        # Remove  navbar 
        pat = re.compile(r'<nav class=.*?</nav>', flags=re.DOTALL)
        pats['navbar'] = pat

        # Remove buttons im bottom of page 
        pat = re.compile(r'<div id="module-widget-2".*?<\/main>', flags=re.DOTALL)
        pats['bottom'] = pat

        # Remove footer
        pat = re.compile(r'<footer id="footer".*?<\/footer>', flags=re.DOTALL)
        pats['footer'] = pat

        # Remove sub-footer
        pat = re.compile(r'<div class="sub-footer".*?sub-footer -->', flags=re.DOTALL)
        pats['sub_footer'] = pat

        # Remove social media
        pat = re.compile(r'^.*?div class="entry-content".*facebook.*?$', flags=re.M)
        pats['social'] = pat

    return pats

def stripText(text, page, pats):
    # Remove title
    text = re.sub(pats['title'], '', text)

    if page == 'rs':
        # Initial comments - very slow!
        text = re.sub(pats['comments'], '<html lang="en-US">', text)

        # Side bar
        text = re.sub(pats['side'], '', text)

        # Remove bottom of page (contact and social media)
        text = re.sub(pats['bottom'], '', text)

        # Remove footer
        text = re.sub(pats['footer'], '', text)

    if page == 'fri':
        # Remove  navbar 
        text = re.sub(pats['navbar'], '', text)

        # Remove buttons im bottom of page 
        text = re.sub(pats['bottom'], '</main>', text)

        # Remove footer
        text = re.sub(pats['footer'], '', text)

        # Remove sub-footer
        text = re.sub(pats['sub_footer'], '', text)

        # Remove social media
        text = re.sub(pats['social'], '', text)
    return text

def getTitle(text, page):
    if page == 'rs':
      pat = r'<title>(.+?)\|'
    if page == 'fri':
      pat = r'<title>(.+?)&'
    title  = re.search(pat, text).group(1)
    pattern = re.compile('[\W_]+')
    title = pattern.sub('',title)
    return title

def fixArticleTOC(text, title):

    pat = r'<div id="toc_container"[\s\S]*?</div>'
    toc = re.search(pat, text).group()

    pat2 = r'"#([\s\S]*?")'
    repl = lambda m: '"#' + title+'-'+m.group(1)
    toc_new = re.sub(pat2, repl, toc)
    text = re.sub(pat, toc_new, text)

    pat3 = r'(<span id=")([\s\S]*?")'
    repl = lambda m: m.group(1) + title+'-'+m.group(2)
    text = re.sub(pat3, repl, text)

    # Convert title to h1 to make it linkable
    pat0 = r'<h1([\s\S]*?)</h1>'
    repl = lambda m: '<h1 id = \"' + title +"\""+ m.group(1) + '</h1>'
    text = re.sub(pat0, repl, text)

    return text

def downloadAndSaveLinksToHtml(links,titles,folder):
    for i,(l,t) in enumerate(zip(links,titles)):
        fname = strvar(i) + '-' + t + '.html'
        # Download article from website
        if not os.path.isfile(folder + '/' + fname):
            print "downloading ", t
            req = urllib2.Request(l, headers={'User-Agent' : "Magic Browser"}) 
            con = urllib2.urlopen(req)
            # This is necessary because the urls sometimes redirect to different pages
            text = con.read()
            Html_file= open(folder + '/' + fname,"w")
            Html_file.write(text)
            Html_file.close()
        else:
            print "already downloaded ", t
        

def stripHtmlFiles(folder):
    folder_stripped = folder + '_stripped'
    if not os.path.exists('./'+folder_stripped):
        os.makedirs('./'+folder_stripped)

    for f in glob.glob(folder + "/*"):
        fname = os.path.basename(f)
        if not os.path.isfile(folder_stripped + '/' + fname):
            fhtml = open(f,'r')
            text = fhtml.read()
            pat = r'<link rel="canonical" href="(.*)"'
            url = re.search(pat, text).group(1)
            if 'reducing-suffering' in url:
                page = 'rs'
            if 'foundational-research' in url:
                page = 'fri'
            print "stripping ", fname
            pats = compileREs(page)
            text = stripText(text, page, pats)
            title = re.search(r'-(.*?).html',fname).group(1)
            print "title: ", title
            text = fixArticleTOC(text,title)
            fstrip = open(folder_stripped + '/' + fname ,"w")
            fstrip.write(text)
            fstrip.close()
        else:
            print "skipping ", fname

def getTOC(text):
    pat = r'(<h2>Introduction</h2>[\s\S]*)<h2 id="Other">Other</h2>'
    toc = re.search(pat,text).group(1)

    # Keep only rs and fri posts
    pat = r'<li>[\s\S]*?</li>'
    repl = lambda m: m.group() if 'foundational' in m.group() or 'http' not in m.group() else ''
    toc = re.sub(pat, repl, toc)

    # Remove translations
    pat_trans= r'(<li>.*?)\([\s\S]*?</li>'
    repl = lambda m: m.group(1)+'</li>'
    toc = re.sub(pat_trans, repl, toc)

    # One special case:
    toc = re.sub(r'Kring et al\.', r'Kring et al. (2013)</a>', toc)

    return toc

i = 0
def updateTOC(toc):
    # Remove "http:/foundational-research.org/" prefix
    pat = r'href=(.*)/([a-zA-Z-]*/)'
    repl = lambda m: 'href=\"'+m.group(2) if 'foundational' in m.group(1) else m.group()
    toc = re.sub(pat, repl, toc)

    # Rename links and remove backslashes for fri links
    pat = r'href="(.*?)(/\"|\")'
    def repl(m):
        global i
        res = 'href=\"' + strvar(i)+'-' +m.group(1) + '.html\"'
        i += 1
        return res
    toc = re.sub(pat, repl, toc)

    pat = r'href="([\s\S]*?)"'
    titles_dash = re.findall(pat,toc)

    pat = r'href="[\s\S]*?>([\s\S]*?)<'
    titles= re.findall(pat,toc)

    return titles_dash, titles, toc

def mergeFiles(ebook_name, folder, header, toc, footer):
    ebook = open(ebook_name,'w')
    ebook.write(header)
    ebook.write(toc)
    for f in glob.glob(folder + "/*"):
        f = open(f)
        text = f.read()
        f.close()
        ebook.write(text)
    ebook.write(footer)
    ebook.close()

def createSeparateTOC(folder, titles, titles_dash):
    toc_template = open('toc_template.html','r')
    toc_text = toc_template.read()
    toc_template.close()
    toc_content = ''
    for t, td in zip(titles, glob.glob(folder + "/*")):
        toc_content += '<a href=\"' + td[len(folder)+1:] + '\">'+t+'</a></br>\n'

    pat = r'TOC'
    toc_text = re.sub(pat,toc_content,toc_text)
    toc = open('toc_sep.html','w')
    toc.write(toc_text)
    toc.close()
    
if __name__ == '__main__':
    url = 'http://reducing-suffering.org/'
    folder = 'bt'
    ebook_name = 'reducing-suffering.html'
    
    parser = argparse.ArgumentParser()
    parser.add_argument('-d','--download', metavar='download', type=int,default=1)
    parser.add_argument('-m','--merge', metavar='merge', type=int,default=0)
    args = parser.parse_args()

    if not os.path.exists('./'+folder):
        os.makedirs('./'+folder)

    response = urllib2.urlopen(url)
    text = response.read()
    toc = getTOC(text)
    links = getLinks(toc,url)
    titles_dash, titles, toc = updateTOC(toc)
    Html_file= open('toc.html',"w")
    Html_file.write(toc)
    Html_file.close()
    if args.download:
        downloadAndSaveLinksToHtml(links,titles_dash,folder)
    stripHtmlFiles(folder)
    with open('header.html','r') as h:
        header = h.read()
    with open('footer.html','r') as f:
        footer = f.read()
    if args.merge == 1:
        mergeFiles(ebook_name, folder, header, toc, footer)
    else:
        createSeparateTOC(folder, titles, titles_dash)
