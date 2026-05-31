import csv
import json
import re
import os

def transform_code(code):
    if not code:
        return "", "OTHER"
    
    code_upper = code.strip().upper()
    
    if code_upper == 'WELCOME':
        return 'W', 'WELCOME'
    
    prefix = code_upper[0]
    rest = code_upper[1:]
    
    mapping = {
        'A': ('D', 'DICTAMEN'),
        'B': ('C', 'CLASES'),
        'C': ('G', 'GENERAL'),
        'D': ('A', 'Alquiler'),
        'E': ('Q', 'QUEJA'),
        'F': ('W', 'WIN'),
        'G': ('T', 'KEYWORD'),
        'H': ('H', 'KEYWORD'),
        'R': ('P', 'PUBLICIDAD')
    }
    
    if prefix in mapping:
        new_prefix, group = mapping[prefix]
        return new_prefix + rest, group
    
    return code_upper, 'OTHER'

def parse_csv(file_path):
    data = {}
    referenced_reminders = set()
    
    with open(file_path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get('codigo'):
                continue
            original_codigo = row['codigo'].strip().upper()
            if not original_codigo:
                continue
            
            mensajes_text = []
            recordatorio_raw = None
            reporte = None
            
            # Combine all message columns
            for i in range(1, 7):
                col_name = f'mensaje {i}'
                val = row.get(col_name)
                if val:
                    val = val.strip()
                    if not val or val.lower() == 'fin':
                        continue
                    
                    # Check for record=...
                    record_match = re.match(r'^record=([^=]+)=(\d+)$', val)
                    if record_match:
                        rem_code_original = record_match.group(1).upper()
                        recordatorio_raw = {
                            'codigo': rem_code_original,
                            'segundos': int(record_match.group(2))
                        }
                        referenced_reminders.add(rem_code_original)
                        continue
                    
                    # Check for reporte=...
                    if val.startswith('reporte='):
                        reporte = val.replace('reporte=', '', 1)
                        continue
                    
                    # If it's just text, add to mensajes
                    mensajes_text.append(val)
            
            data[original_codigo] = {
                'mensajes': mensajes_text,
                'recordatorio_raw': recordatorio_raw,
                'reporte': reporte
            }
    return data, referenced_reminders

def build_recursive_messages(data):
    def get_message_obj(codigo_original, visited=None):
        if visited is None:
            visited = set()
        
        codigo_original = codigo_original.upper()
        
        if codigo_original in visited:
            return None
        
        if codigo_original not in data:
            return None
        
        raw = data[codigo_original]
        obj = {}
        if raw['mensajes']:
            obj['mensajes'] = raw['mensajes']
        
        if raw['reporte']:
            obj['reporte'] = raw['reporte']
            
        if raw['recordatorio_raw']:
            rem_code_orig = raw['recordatorio_raw']['codigo']
            rem_seconds = raw['recordatorio_raw']['segundos']
            
            visited.add(codigo_original)
            rem_content = get_message_obj(rem_code_orig, visited.copy())
            
            recordatorio_obj = {
                'segundos': rem_seconds
            }
            if rem_content:
                recordatorio_obj.update(rem_content)
            
            obj['recordatorio'] = recordatorio_obj
        
        return obj
    return get_message_obj

def main():
    csv_file = 'ATENCIÓN  - codigo_mensaje_v2.csv'
    json_file = 'mensajes.json'
    
    if not os.path.exists(csv_file):
        print(f"Error: {csv_file} not found.")
        return
        
    raw_data, referenced_reminders = parse_csv(csv_file)
    get_message_obj = build_recursive_messages(raw_data)
    
    grouped_output = {}
    
    for original_codigo in raw_data:
        # Only list messages that are NOT used as reminders
        if original_codigo not in referenced_reminders:
            new_codigo, group_name = transform_code(original_codigo)
            
            msg_obj = get_message_obj(original_codigo)
            if msg_obj:
                # Specific logic for T2, T3, T4 in KEYWORD group
                if group_name == 'KEYWORD' and new_codigo in ['T2', 'T3', 'T4']:
                    msg_obj['segundos'] = 7200
                
                if group_name not in grouped_output:
                    grouped_output[group_name] = {}
                grouped_output[group_name][new_codigo] = msg_obj
    
    # Sort groups and codes for cleaner output
    sorted_output = {k: grouped_output[k] for k in sorted(grouped_output.keys())}
    
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(sorted_output, f, indent=4, ensure_ascii=False)
    
    print(f"Successfully created {json_file}")

if __name__ == "__main__":
    main()
