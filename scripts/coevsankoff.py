import numpy as np
from collections import Counter
import multiprocessing as mp
import pandas as pd
from Bio import SeqIO
import pandas as pd
from colour import Color
import dill as pickle
import time
import h5py
import dendropy
from Bio import AlignIO , SeqIO , Seq
from Bio.Alphabet import generic_dna

from scipy import sparse
import pickle
import multiprocessing as mp
import copy

import sys
import os

import itertools

print('mk8_codon')

sys.setrecursionlimit( 10 **5 )
runName = 'COEV_cleantree_mk6'
NCORE = 50
treefile = '/home/cactuskid13/covid/lucy_mk3/gisaid_hcov-2020_08_25.QC.NSoutlier.filter.deMaiomask.aln.EPIID.treefile'
alnfile = '/home/cactuskid13/covid/lucy_mk3/gisaid_hcov-2020_08_25.QC.NSoutlier.filter.deMaiomask.EPIID.aln'

#use blast based annotation to assign codons to column ranges
annotation = pd.read_csv( alnfile +'annotation.csv' )

#fraction of genomes to remove if jackknifing
bootstrap = .33
#number of replicates
bootstrap_replicates = 1
restart = None

#keep track of transitions and not just events as binary

transition_matrices = True
allowed_symbols = [ b'A', b'C', b'G' , b'T' ]
allowed_transitions = [ c1+c2 for c1 in allowed_symbols for c2 in allowed_symbols  if c1!= c2]
print(allowed_transitions)

transition_dict = {  c : i  for i,c in enumerate( allowed_transitions )  }
rev_transition_dict= dict( zip(transition_dict.values(), transition_dict.keys()))
allowed_symbols = set(allowed_symbols)
print( transition_dict)

tree = dendropy.Tree.get(
    path=treefile,
    schema='newick')



msa = AlignIO.read(alnfile , format = 'fasta')
if os.path.exists(alnfile +'.h5'):
    with h5py.File(alnfile +'.h5', 'r') as hf:
        align_array = hf['MSA2array'][:]
        #implement w np unique could be faster
else:
    print('aln 2 numpy ')
    align_array = np.array([ list(rec.upper())  for rec in msa], np.character)
    print('done')
    print('dumping to hdf5')
    with h5py.File(alnfile +'.h5', 'w') as hf:
        hf.create_dataset("MSA2array",  data=align_array)
    print('done')


print('selecting informative sites')
#find all sites with mutations
sites = {}

#todo... paralellize this
for col in range(align_array.shape[1]):
    if col % 1000  == 0:
        print(col)
    (unique, counts) = np.unique(align_array[:,col].ravel() , return_counts=True)
    sites.update({col:dict(zip(list(unique), list(counts)))})
informativesites = set([ s for s in sites if len( set( sites[s].keys()) -set([b'-',b'N']) ) > 1  ] )
print(len(informativesites))

print('done')
#associate informative sites to a codon
codon_dict = {}

print( 'grouping codons')
for i,r in annotation.iterrows():
    #indexing starts at 1 for blast
    for j,codon in enumerate(range(r.qstart-1, r.qend-1 , 3 )):
        for nt in [codon,codon+ 1, codon+2]:
            if nt in informativesites:
                if (codon,codon+2) not in codon_dict:
                    codon_dict[(codon,codon+2)] = (nt,)
                else:
                    codon_dict[(codon,codon+2)]+ (nt,)
print('done')



codon_dict_rev = dict(zip ( codon_dict.values() , codon_dict.keys( ) ) )
def clipID(ID):
    return ID.replace('|',' ').replace('_',' ').replace('/',' ').strip()


print('preparing tree')
IDs = {i:clipID(rec.id) for i,rec in enumerate(msa)}
#IDs = {i:rec.id for i,rec in enumerate(msa)}
IDindex = dict(zip( IDs.values() , IDs.keys() ) )
print( [(t,IDindex[t]) for t in list(IDindex.keys())[0:10]] )


matsize = len(tree.nodes())
print('done')
print(matsize)
print('nodes')


def process_node_smallpars_1(node):
    #go from leaves up and generate character sets
    if node.symbols is None:
        for child in node.child_nodes():
            if child.symbols is None:
                process_node_smallpars_1(child)
        node.symbols = { }
        node.scores = { }

        for pos in [0,1,2]:
            symbols = set.intersection( * [ child.symbols[pos] for child in node.child_nodes( ) ] )
            if len(symbols) == 0:
                symbols = set.union( * [ child.symbols[pos] for child in node.child_nodes( ) ] )

            node.symbols[pos] = symbols
            node.scores[pos] = { }
            for c in allowed_symbols:
                if c not in node.symbols[pos]:
                    #add trnasition mat here if needed
                    score = min(  [ child.scores[pos][c] for child in node.child_nodes()])+1
                else:
                    score = min(  [ child.scores[pos][c] for child in node.child_nodes() ] )
                node.scores[pos][c] = score

def process_node_smallpars_2(node):
    #assign the most parsimonious char from children
    if node.char is None:
        if node.parent_node:
            #node has parent
            node.char = {}
            node.event = {}
            node.eventype= {}
            node.AAevent = 0
            for pos in [0,1,2]:
                node.char[pos] = min(node.scores[pos], key=node.scores[pos].get)
                if node.parent_node.char[pos] == node.char[pos]:
                    node.event[pos] = 0
                else:
                    if node.scores[pos][node.parent_node.char[pos]] == node.scores[pos][node.char[pos]] :
                        node.char[pos] = node.parent_node.char[pos]
                        node.event[pos] = 0
                    else:
                        node.event[pos] = 1
                        node.eventype[pos] = transition_dict[node.parent_node.char[pos]+node.char[pos]]

            node.AA = Seq.Seq(b''.join([ node.char[pos] for pos in [0,1,2] ]).decode(), generic_dna ).translate()
            if node.AA != node.parent_node.AA:
                node.AAevent = 1

        else:
            #root node
            node.char = {}
            node.event= {}
            node.eventype = {}
            node.AAevent = 0
            for pos in [0,1,2]:

                node.char[pos] = min(node.scores[pos], key=node.scores[pos].get)
                node.event[pos] = 0

            node.AA = Seq.Seq(b''.join([ node.char[pos] for pos in [0,1,2] ]).decode(), generic_dna ).translate()
        #down one level
        for child in node.child_nodes():
            if child.char is None:
                process_node_smallpars_2(child)

def calculate_small_parsimony( t, aln_columns , row_index , iolock, verbose  = True ):

    missing = 0
    #assign leaf values
    print(aln_columns)
    for pos,col in enumerate(aln_columns):
        for l in t.leaf_nodes():
            if hasattr(col , 'decode'):
                #column has no events
                l.calc[pos] = False
                char = col
                l.event[pos] = 0
                l.scores[pos] = { c:10**10 for c in allowed_symbols }
                if char.upper() in allowed_symbols:
                    l.symbols[pos] = { char }
                    l.scores[pos][char] = 0
                else:
                    #ambiguous leaf
                    l.symbols[pos] = allowed_symbols
            else:

                #setup for small_pars1
                l.calc[pos] = True
                l.event[pos] =0
                l.scores[pos] = { c:10**10 for c in allowed_symbols }

                if str(l.taxon).replace("'", '') in row_index:

                    char = col[ row_index[str(l.taxon).replace("'", '')]  ]
                    if char.upper() in allowed_symbols:
                        l.symbols[pos] = { char }
                        l.scores[pos][char] = 0
                    else:
                        #ambiguous leaf
                        l.symbols[pos] =  allowed_symbols
                else:
                    missing += 1
                    char = None
                    l.symbols[pos] =  allowed_symbols

                    if verbose == True:
                        with iolock:
                            print( 'err ! alncol: ', l.taxon , aln_column  )
                l.char[pos] = min(l.scores[pos], key=l.scores[pos].get)

    #up
    process_node_smallpars_1(t.seed_node)
    #down
    process_node_smallpars_2(t.seed_node)
    eventdict = {}
    for pos in [0,1,2]:
        eventindex = [ n.matrow for n in t.nodes() if n.event[pos] > 0 ]
        eventtypes = [ n.eventype[pos] for n in t.nodes() if n.event[pos] > 0 ]
        eventdict[pos] = { 'type': eventtypes , 'index' : eventindex }
    AAeventindex = [ n.matrow for n in t.nodes() if n.AAevent > 0 ]

    if verbose == True:
        with iolock:
            print('smallpars done')
            print(eventdict)
            print(AAeventindex)
    return (eventdict , AAeventindex)

def process( q , retq, iolock , tree , IDindex ):
    #calculate tree events
    with iolock:
        print('init worker')
    count = 0
    init = False
    while True:
        stuff = q.get()
        if stuff is None and init == True:
            break
        index,aln_column = stuff
        #first data received
        init = True
        retq.put( ( index , calculate_small_parsimony( copy.deepcopy(tree) , aln_column , IDindex , iolock ) ) )
        if count % 1000 == 0:
            with iolock:
                print(index)
                print('worker')
        count+= 1
    print('done')

def mat_creator(retq,matsize,iolock, runName, datasize , verbose = False , restart = None ):
    runtime = time.time()
    with iolock:
        print('init matcreator')
        print(runName)
    if restart == True:
        if transition_matrices == True:
            with open(restart, 'rb') as pklin:
                count, transiton_sparsemats.loads(pklin.read())
        else:
            with open(restart, 'rb') as pklin:
                count , M1 = pickle.loads(pklin.read())
    else:
        count = 0
        AAmutation = sparse.csc_matrix((matsize[0],matsize[1]) , dtype=np.int32 )

        if transition_matrices == True:
            transiton_sparsemats = {}
            for c in transition_dict:
                transiton_sparsemats[c] = sparse.csc_matrix((matsize[0],matsize[1] ) ,dtype=np.int32)
        else:
            M1 = sparse.csc_matrix((matsize[0],matsize[1]) , dtype=np.int32 )
    init = False
    t0 = time.time()
    while True:
        r = retq.get()
        count+=1
        column,events = r
        eventdict ,AAeventindex  = events
        if verbose == True:
            print( eventdict , AAeventindex )

        #save each position to event mats
        for pos in [0,1,2]:

            col = column+pos


            eventindex = eventdict[pos]['index']
            eventtypes = eventdict[pos]['type']

            if len(eventindex)>0:
                if verbose == True:
                    print( eventindex , eventtypes)
                if transition_matrices == True:
                    transition_types = np.unique(eventtypes)
                    for e in list(transition_types):
                        typeindex = np.where( eventtypes == e )[0]
                        selectrows = np.array(eventindex)[typeindex]
                        transiton_sparsemats[rev_transition_dict[e]]  += sparse.csc_matrix( ( np.ones(len(selectrows))  , (selectrows , np.ones(len(selectrows )) * col ) ) , shape= (matsize[0] , matsize[1] ) ,  dtype = np.int32 )
                else:
                    M1 += sparse.csc_matrix( ( np.ones(len(eventindex))  , (eventindex , np.ones(len(eventindex)) * col ) ) , shape= (matsize[0] , matsize[1] ) ,  dtype = np.int32 )
        AAmutation  += sparse.csc_matrix( ( np.ones(len(AAeventindex))  , (AAeventindex , np.ones(len(AAeventindex)) * column ) ) , shape= (matsize[0] , matsize[1] ) ,  dtype = np.int32 )
        if count >= datasize:
            with iolock:
                print('final save')
                print( time.time()- runtime )
                if transition_matrices == True:
                    for transition in transiton_sparsemats:
                        print( 'saving ' , transition)
                        with open( runName + str(transition)+ '_coevmat_transitionmatrices.pkl' , 'wb') as coevout:
                            transiton_sparsemats[transition].sum_duplicates()
                            coevout.write(pickle.dumps((count,transiton_sparsemats[transition])))
                    with open( runName + '_coevmat_AAmutations.pkl' , 'wb') as coevout:
                        AAmutation.sum_duplicates()
                        coevout.write(pickle.dumps((count,AAmutation)))
                else:
                    with open( runName + 'coevmat.pkl' , 'wb') as coevout:
                        coevout.write(pickle.dumps((count,M1)))
                print('DONE')
            return
        if time.time()-t0> 1200:
            t0 = time.time()
            with iolock:
                print('saving')
                print( time.time()- runtime )
                if transition_matrices == True:
                    for transition in transiton_sparsemats:
                        print( 'saving ' , transition)
                        with open( runName +'_transition_' + str(transition)+ '_coevmat.pkl' , 'wb') as coevout:
                            transiton_sparsemats[transition].sum_duplicates()
                            coevout.write(pickle.dumps((count,transiton_sparsemats[transition])))
                else:
                    with open( runName + 'coevmat.pkl' , 'wb') as coevout:
                        M1.sum_duplicates()
                        coevout.write(pickle.dumps((count,M1)))
                print('done')




def main(runName , align_array , replicates = None, bootstrap = None , restart = None ):
    #reset tree
    for i,n in enumerate(tree.nodes()):
        n.matrow = i
        n.symbols = None
        n.scores = None
        n.event = None
        n.char = None
        n.eventype = None
        n.AAevent = 0

    for i,l in enumerate(tree.leaf_nodes()):
        l.event = {}
        l.scores = {}
        l.symbols = {}
        l.char= {}
        l.calc = {}

    #our results will be events on tree nodes for each column
    matsize = (len(tree.nodes()),align_array.shape[1])
    q = mp.Queue(maxsize=NCORE*1000)
    retq = mp.Queue(maxsize= NCORE*100 )
    iolock = mp.Lock()
    #start workers
    pool = mp.Pool(NCORE, initializer=process, initargs=(q,retq, iolock ,tree , IDindex ))
    #start saver
    outname = alnfile + runName
    if bootstrap:
        outname+='bootstrap_run'

    if bootstrap:
        #rerun analysis here
        datasize = replicates * len( informativesites )

        p = mp.Process( target=mat_creator, args=(retq,matsize, iolock, outname , datasize , restart ))
        p.start()

        for n in range(replicates):
            #select portion of random genomes to take out
            del_genomes = np.random.randint( align_array.shape[0], size= int(align_array.shape[0]*bootstrap) )
            for i,r in annotation.iterrows():
                #indexing starts at 1 for blast
                for j,codon in enumerate(range(r.qstart-1, r.qend-1 , 3 )):
                    positions = []
                    for nt in [codon,codon+ 1, codon+2]:
                        if nt in informativesites:
                            s1 = align_array[:,nt]
                            #change characters of random genomes to N
                            s1[del_genomes] = b'N'
                            positions.append(s1)
                        else:
                            #just add the alignment character if it doesnt change.
                            positions.append(   align_array[0,nt] )
                    q.put( (j,positions ) )

    else:
        datasize = len( informativesites )
        p = mp.Process(target=mat_creator, args=(retq,matsize, iolock, outname, datasize ))
        p.start()
        for i,r in annotation.iterrows():
            positions = []
            #indexing starts at 1 for blast
            for j,codon in enumerate(range(r.qstart-1, r.qend-1 , 3 )):
                for nt in [codon,codon+ 1, codon+2]:
                    if nt in informativesites:
                        s1 = align_array[:,nt]
                        positions.append(s1)
                    else:
                        #just add the alignment character if it doesnt change.
                        postions.append(align_array[0,nt] )
                q.put( (j,positions) )

    for _ in range(2*NCORE):  # tell workers we're done
        q.put(None)
    pool.close()
    pool.join()
    print('workers done')
    for _ in range(2*NCORE):  # tell workers we're done
        retq.put(None)
    p.join()
    print('saver done')
    del q
    del retq
    print('DONE!')

if __name__ == '__main__':
    if bootstrap:
        main(runName+'bootstrap_run' , align_array , bootstrap_replicates , bootstrap , restart = restart )
    ## TODO: merge bootstrap replicates into one event mat

    else:
        main(runName+"full" , align_array , restart = restart )
