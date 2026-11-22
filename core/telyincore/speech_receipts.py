"""Match declared protocol acceptance to one durable tool invocation.

Acceptance is not proof that a person read a message. Legacy unpaired calls,
ambiguous references and queued intents confer no participation authority.
"""
import json
import re
import string


def confirmed_speech_calls(records, channels):
    declarations={}
    for channel in channels or ():
        if not isinstance(channel,dict):continue
        speech=channel.get('speech')
        if not isinstance(speech,dict):continue
        template=speech.get('receipt')
        if not isinstance(template,str) or not 0<len(template)<=1024:continue
        for name in speech.get('tools') or ():
            if isinstance(name,str):declarations.setdefault(name,set()).add(template)
    grouped={}
    for index,row in enumerate(records):
        ref=getattr(row,'tool_ref','')
        if not isinstance(ref,str) or not ref:continue
        grouped.setdefault(ref,[]).append((index,row))
    confirmed=set()
    for pair in grouped.values():
        if len(pair)!=2:continue
        (_,call),(_,result)=pair
        if (getattr(call,'kind',None)!='function_call' or getattr(result,'kind',None)!='function_result'
            or getattr(call,'role',None)!='assistant' or getattr(result,'role',None)!='assistant'
            or getattr(call,'name',None)!=getattr(result,'name',None)
            or getattr(call,'scope','')!=getattr(result,'scope','')):continue
        templates=declarations.get(call.name,())
        if len(templates)!=1:continue
        try:args=json.loads(call.text)
        except (ValueError,TypeError):continue
        if not isinstance(args,dict):continue
        pattern=[];receipt_fields=0;valid=True
        try:
            for literal,field,fmt,conversion in string.Formatter().parse(next(iter(templates))):
                pattern.append(re.escape(literal))
                if field is None:continue
                if fmt or conversion:valid=False;break
                if field=='receipt_id':
                    receipt_fields+=1;pattern.append(r'-?[1-9][0-9]{0,19}')
                elif re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',field) and type(args.get(field)) in (str,int):
                    value=str(args[field])
                    if not 0<len(value)<=128:valid=False;break
                    pattern.append(re.escape(value))
                else:valid=False;break
        except ValueError:continue
        text=getattr(result,'text',None)
        if (valid and receipt_fields==1 and isinstance(text,str) and len(text)<=4096
                and re.fullmatch(''.join(pattern),text)):
            confirmed.add(id(call))
    return confirmed
