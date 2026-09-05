import sqlite3
import os
import shutil

DB = r'C:\Users\91880\.copilot\repos\sdm\instance\sdm.sqlite'
UPLOADS = r'C:\Users\91880\.copilot\repos\sdm\instance\uploads'

def main():
    if not os.path.exists(DB):
        print('Database not found:', DB)
        return
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    for t in ('attachments','workflow_events','sdm'):
        try:
            cnt = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f'{t} count before: {cnt}')
        except Exception as e:
            print('Table', t, 'missing or error:', e)
    try:
        c.execute('DELETE FROM attachments')
        c.execute('DELETE FROM workflow_events')
        c.execute('DELETE FROM sdm')
        conn.commit()
        print('Deleted rows from attachments, workflow_events, sdm')
    except Exception as e:
        print('Error deleting rows:', e)
    for t in ('attachments','workflow_events','sdm'):
        try:
            cnt = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f'{t} count after: {cnt}')
        except Exception as e:
            print('Table', t, 'missing or error:', e)
    conn.close()

    # Remove upload files
    if os.path.exists(UPLOADS):
        for name in os.listdir(UPLOADS):
            path = os.path.join(UPLOADS, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
                print('Removed directory', path)
            else:
                try:
                    os.remove(path)
                    print('Removed file', path)
                except Exception as e:
                    print('Failed to remove', path, e)
        try:
            if not os.listdir(UPLOADS):
                os.rmdir(UPLOADS)
                print('Removed uploads directory')
        except Exception as e:
            print('Cleanup error:', e)

if __name__ == '__main__':
    main()
